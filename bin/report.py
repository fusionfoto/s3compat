#!/usr/bin/env python

from xml.etree import ElementTree
from collections import defaultdict, Counter
from tabulate import tabulate
from copy import deepcopy
import argparse
import yaml
import sys
import csv

result_types = ('PASS', 'NEW_FAILURE', 'KNOWN_FAILURE', 'SKIP',)


def dict_merge(a, b):
    if not isinstance(b, dict):
        return b

    result = deepcopy(a)
    for k, v in b.iteritems():
        if k in result and isinstance(result[k], dict):
            result[k] = dict_merge(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result


def load_known_failures(kf_fn):
    with open(kf_fn, 'r') as kf_file:
        return yaml.load(kf_file)


def extract_message(node):
    text = node.attrib['message']
    if not text:
        return None

    end = text.find('\n')
    if end != -1:
        return text[:end]
    else:
        return text[:20] + '...'


def load_nose_xml(test_file):
    results = {}

    test_result_xml = ElementTree.parse(test_file)

    for test in test_result_xml.findall('testcase'):
        result = 'PASS'
        message = None
        name = test.attrib['classname'] + '.' + test.attrib['name']
        failures = test.findall('failure')
        errors = test.findall('error')
        skipped = test.findall('skipped')

        # TODO: Condense or differentiate
        if failures:
            assert len(failures) == 1
            message = extract_message(failures[0])
            result = 'FAIL'

        if errors:
            assert len(errors) == 1
            message = extract_message(errors[0])
            result = 'FAIL'

        if skipped:
            assert len(skipped) == 1
            result = 'SKIP'

        results[name] = dict(name=name,
                             result=result,
                             message=message,
                             time=test.attrib['time'])

    return results


def csv_report(results):
    fieldnames = ['name', 'result', 'report', 'message']
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)

    writer.writeheader()
    for _, row in results.iteritems():
        writer.writerow(row)


def summary_report(results, detailed):
    by_report_status = defaultdict(list)
    for test, rec in results.iteritems():
        by_report_status[rec.get('report')].append(rec)

    by_failure_message = Counter(r['message'] for t, r in
                                 results.iteritems() if r['message'])

    if not detailed:
        table = []
        for s in ('PASS', 'NEW_FAILURE', 'KNOWN_FAILURE', 'SKIP'):
            table.append((s, len(by_report_status[s])))

        print tabulate(table)
        print("TOTAL TESTS:   %d" % len(results))
        print
    print "10 most common failures:"
    print tabulate(by_failure_message.most_common(10))
    print
    print "10 longest-running tests:"
    print tabulate(
        (t, r['time']) for t, r in sorted(
            results.items(),
            key=lambda x: float(x[1]['time']),
            reverse=True)[:10])
    print
    print "NEW_FAILURE:"
    for test in by_report_status['NEW_FAILURE']:
        print test['name']


def result_passed(passed, total):
    # Calculate perecentage and raw totals
    return ["%.1f%%" % (float(passed)/total*100),
            '%d/%d' % (passed, total)] if total else ['N/A']


def get_row(name, elements, totals, results, codes, all_codes):
    row = [name]
    result_counter = dict.fromkeys(result_types, 0)
    current_codes = set()
    for element in elements:
        if element not in results:
            continue
        result_counter[results[element]['report']] += 1
        try:
            code = kfs[results[element]['name']]['code']
            if code in codes:
                if code not in current_codes:
                    current_codes.add(code)
        except KeyError:
            pass
        if results:
            del results[element]

    for result_type in result_types:
        row.append(result_counter[result_type])
        totals[result_type] += result_counter[result_type]

    row += result_passed(result_counter['PASS'],
                         sum(result_counter.values()))

    # Notes column, formatted for mediawiki
    code_string = ""
    for code in sorted(current_codes):
        if code not in all_codes:
            code_string = '<ref name="%s">%s</ref>' % (code, codes[code])
            all_codes.add(code)
        else:
            # Only need footnote for first instance
            code_string = '<ref name="%s"/>' % code
    row.append(code_string)

    return row


def detailed_results_table(results, attributes, kfs, codes):
    table = [["Category", 'Pass', 'New Failure',
              'Known Failure', 'Skip', "Pass %", "Tests Passed", 'Notes']]

    totals = dict.fromkeys(result_types, 0)

    results = results.copy()
    all_codes = set()

    flag_table = []
    if 'flags' in attributes:
        for flag in sorted(attributes['flags']):
            row = get_row(flag, attributes['flags'][flag],
                          totals, results, codes, all_codes)
            flag_table.append(row)

    for method in sorted(attributes['method'], key=str.lower):
        for resource in sorted(attributes['resource'], key=str.lower):
            intersection = (attributes['method'][method].
                            intersection(attributes['resource'][resource]))
            if intersection:
                old_length = len(results)
                row = get_row("%s %s" % (method, resource), intersection,
                              totals, results, codes, all_codes)
                if old_length - len(results):
                    table.append(row)

    table += flag_table

    row = get_row("other", results.copy(), totals, results, codes, all_codes)
    table.append(row)

    table.append(["Total"] + (list(totals[result_type] for result_type
                              in result_types) + result_passed(totals['PASS'],
                              sum(totals.values())))+[None])

    return table


def detailed_report_console(results, attributes, kfs, codes):
    table = detailed_results_table(results, attributes, kfs, codes)
    # Hide Notes column
    columns = (True, True, True, True, True, True, True, False)
    table = [[v for i, v in enumerate(row) if columns[i]] for row in table]
    print tabulate(table, headers="firstrow", tablefmt='simple')


def detailed_report_wiki(results, attributes, kfs, codes):
    table = detailed_results_table(results, attributes, kfs, codes)
    columns = (True, False, False, False, False, True, True, True)

    table = [[v for i, v in enumerate(row) if columns[i]] for row in table]

    print ('== Amazon S3 REST API Compatability using '
           '[https://github.com/ceph/s3-tests Ceph s3-tests] ==')
    print tabulate(table, headers="firstrow", tablefmt='mediawiki')
    print "<references />"


parser = argparse.ArgumentParser()
parser.add_argument('-f', '--format', choices=['summary', 'csv'],
                    help='output format', default='summary')
parser.add_argument('-d', '--detailed',
                    help='output detailed information using attribute file')
parser.add_argument('-df', '--detailedformat', choices=['console', 'wiki'],
                    help='output detailed information format',
                    default='console')
parser.add_argument('-kf', '--known-failures', action='append',
                    help='known-failure file', default=[])
parser.add_argument('test_results',
                    help='test result file')
args = parser.parse_args()

results = load_nose_xml(args.test_results)

all_kfs = {}
for kf in args.known_failures:
    all_kfs = dict_merge(all_kfs, load_known_failures(kf))

kfs = all_kfs.get('ceph_s3', {})
for test, rec in results.iteritems():
    rec['report'] = 'PASS'

    if rec['result'] == 'SKIP':
        rec['report'] = 'SKIP'
        continue

    if test not in kfs:
        if rec['result'] == 'FAIL':
            rec['report'] = 'NEW_FAILURE'
        continue

    if rec['result'] == 'FAIL':
        status = kfs[test]['status']
        if status == 'KNOWN':
            rec['report'] = 'KNOWN_FAILURE'

codes = all_kfs.get('codes', {})

if args.detailed:
    try:
        with open(args.detailed, 'r') as yaml_loading:
            attributes = yaml.load(yaml_loading)
    except IOError:
        print 'Unable to open detailed results attribute file'
        raise
    else:
        if args.detailedformat == 'console':
            detailed_report_console(results, attributes, kfs, codes)
        elif args.detailedformat == 'wiki':
            detailed_report_wiki(results, attributes, kfs, codes)
        else:
            # We should never get here -- argparse should catch errors
            raise ValueError("format must be 'console' or 'wiki'")
if args.format == 'summary':
    summary_report(results, args.detailed)
elif args.format == 'csv':
    csv_report(results)
else:
    # We should never get here -- argparse should catch errors
    raise ValueError("format must be 'summary' or 'csv'")