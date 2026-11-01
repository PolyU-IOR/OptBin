#!/usr/bin/env python3
"""Refresh marked technical-report evidence and optionally compile/render it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from summarize_gurobi import merge_studies

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--update-only', action='store_true')
    args = parser.parse_args()
    sources = {}

    def read(name):
        path = ROOT / 'benchmarks' / name
        data = path.read_bytes()
        sources[str(path.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
        return json.loads(data)

    correctness = read('real-correctness-gurobi-20261030.json')
    watermark=read('reader-rss-watermark-20261030.json')['observations']
    assert len(watermark)==2 and watermark[1]['retained_parent_bytes']==128*1024*1024
    assert watermark[1]['child_read_peak_rss_bytes']-watermark[0]['child_read_peak_rss_bytes']>120*1024*1024
    solvers = read('real-solvers.json')
    small = read('gzip-readers-20261029.json'); large = read('gzip-large-20261029.json')
    if large.get('study_reports'):
        combined=[]
        for entry in large['study_reports']:
            child=read(Path(entry['path']).name)
            assert sources[entry['path']]==entry['sha256']
            assert child['complete']
            combined.extend(child['results'])
        assert combined==large['results']
    boundary = read('large-boundary.json'); inputs = read('large-inputs.json')
    passed = [x for x in correctness['results'] if x['status'] == 'pass']
    excluded = [x for x in correctness['results'] if x['status'] == 'excluded']
    assert len(passed) == 22 and len(excluded) == 6
    mps_passed=[x for x in passed if x['format']=='mps']
    assert len(mps_passed)==13 and all(x['source_features'] and not any(x['source_features'].values()) for x in mps_passed)
    assert all(x['rejection_verified'] for x in excluded)
    assert len(inputs['instances']) == 8
    assert all(x['payload_sha256_verified_this_run'] and
               x['observed_payload_sha256'] == x['bqp_sha256_from_existing_manifest'] for x in inputs['instances'])
    assert boundary['nnz_A'] > 2**32 and boundary['row_index_bytes'] == 4
    assert boundary['comparison']['bitwise_reference_equal']
    assert small['complete'] and large['complete']
    assert len(small['results']) == 22 and len(large['results']) == 2
    correct_by_id={x['id']:x for x in passed}
    for model in small['results']:
        checked=correct_by_id[model['id']]
        assert checked['input_sha256']==model['source_sha256']
    for study, repeats in [(small, 7), (large, 3)]:
        for model in study['results']:
            assert model['status'] == 'complete'
            assert all(x['bitwise_reference_equal'] for x in model['verification'])
            for layout in model['formats']:
                skipped = study is large and layout['format'] == 'text-gzip'
                assert len(layout['runs']) == (0 if skipped else repeats)
                assert all(x['numerical_sha256'] == model['numerical_sha256'] for x in layout['runs'])
    gurobi_small=read('gurobi-small-20261030.json');gurobi_large=read('gurobi-large-20261030.json')
    mosek_small=read('mosek-cbf-20261030.json')
    if gurobi_large.get('study_reports'):
        rows=[]
        for entry in gurobi_large['study_reports']:
            child=read(Path(entry['path']).name)
            assert child['complete'] and sources[entry['path']]==entry['sha256']
            assert {k:v for k,v in child['environment'].items() if k!='started_utc'}=={
                k:v for k,v in gurobi_large['environment'].items() if k!='started_utc'}
            rows.extend(child['results'])
        assert rows==gurobi_large['results']
    assert gurobi_large['execution_policy']['concurrent_case_jobs']==2
    assert gurobi_small['baseline_report']['sha256']==sources['benchmarks/gzip-readers-20261029.json']
    assert gurobi_large['baseline_report']['sha256']==sources['benchmarks/gzip-large-20261029.json']
    assert mosek_small['baseline_report']['sha256']==sources['benchmarks/gzip-readers-20261029.json']
    small,large=merge_studies(small,large,gurobi_small,gurobi_large,mosek_small)
    api_boundary=read('gurobi-api-boundary-20261030.json')
    assert api_boundary['observed_query_failure']['read_succeeded']
    assert api_boundary['workaround']['full_numerical_comparison_passed']
    assert api_boundary['workaround']['report_sha256']==sources['benchmarks/gurobi-large-20261030.json']
    verified_prod=next(v for v in large['results'][0]['verification'] if v['format']=='gurobi-mps-gzip')
    assert api_boundary['workaround']['matrix_nnz_checked']==verified_prod['matrix_nnz_checked']
    assert api_boundary['workaround']['matrix_row_partitions']==verified_prod['matrix_row_partitions']
    profiles=read('gurobi-large-profiles-20261030.json')
    assert profiles['complete'] and len(profiles['results'])==2
    for entry in profiles['study_reports']:
        assert sources[entry['path']]==entry['sha256']
    audited=set()
    for entry in profiles['results']:
        assert entry['id'] not in audited;audited.add(entry['id'])
        case=next(c for c in gurobi_large['results'] if c['id']==entry['id'])
        verified=next(v for v in case['verification'] if v['format']=='gurobi-mps-gzip')
        actual=entry['read_result']
        assert entry['profile_pass'] and entry['matches_fully_verified_model']
        assert entry['source_sha256']==next(f['sha256'] for f in case['formats'] if f['format']=='gurobi-mps-gzip')
        assert all(actual[k]==verified[k] for k in ['rows','cols','nnz','gurobi_fingerprint','gurobi_version'])
        assert actual['objectives']==1
        assert not any(actual[k] for k in ['is_mip','is_multiobjective','integer_variables',
            'general_constraints','sos_constraints','pwl_objective_variables','q_nnz','q_constraints'])
    text = (ROOT / 'python/optbin/__init__.py').read_text()
    version = re.search(r'__version__ = "([^"]+)"', text)[1]
    escape = lambda s: str(s).replace('_', r'\_').replace('%', r'\%').replace('&', r'\&')
    runs = sum(len(x['results']) for x in solvers['results'])
    pairs = [x for x in solvers['results'] if x['results'][0]['engine'] == 'source-gurobi']
    assert len(pairs) == 13
    assert all(y['status'] == 2 for x in pairs for y in x['results']
               if y['engine'] in ['source-gurobi', 'gurobi'])
    regions = {'VERSION': '\\date{Version ' + version + ' -- 30 October 2026}',
        'VALIDATION':
            f'Complete numerical comparisons with independent source readers pass on {len(passed)} continuous models '
            '(13 MPS LP/QP/QCQP models read independently by Gurobi and nine CBFs by both MOSEK and the original CBLIB parser). '
            f'All {len(excluded)} selected integer CBFs are rejected explicitly. '
            f'The archived solver study contains {runs} source/native runs; all 13 paired Gurobi cases return optimal status. '
            'Some conic/SDP runs are inaccurate or inconclusive and two SCIP runs reach their time limits. '
            'All eight large source BQP payload SHA-256 hashes match their existing manifest.',
        'INDEX_BOUNDARY':
            r'A separate full load of \texttt{' + escape(boundary['id']) + r'} reconstructs $' +
            f"{boundary['nnz_A']:,}".replace(',', r'{,}') +
            r'$ entries exactly against its BQP source, retaining 32-bit row indices and 64-bit pointers. '
            'This verifies an entry count beyond $2^{32}$; it is a correctness-only check, not a repeated timing result.'}
    if (ROOT / 'benchmarks/jump-correctness.json').exists():
        jump = read('jump-correctness.json')
        real = [x for x in jump['results'] if not x['fixture']]
        assert len(real) == 22 and all(x['status'] == 'pass' for x in real)
        assert jump['summary'] == {'pass': 37, 'known_reference_discrepancy': 5, 'reference_unsupported': 1}
        regions['VALIDATION'] += (
            '\n\nAn additional JuMP ' + jump['environment']['packages']['JuMP']['version'] +
            '/MOI ' + jump['environment']['packages']['MathOptInterface']['version'] +
            ' source-reader check matches all 22 real models and 15 fixture/configuration cases after explicit '
            'function-in-set normalization. Five targeted MPS fixtures retain reader discrepancies, adjudicated '
            'against MOSEK or Gurobi; one generalized power cone is unsupported by MOI. These six cases are not '
            'agreement passes. Details and pinned reproduction are in \\path{docs/jump-validation.md}.')
    layouts = [('Text.gz', 'text-gzip'), ('raw.h5.gz', 'raw-hdf5-gzip'),
               ('OptBin', 'optbin'), ('OptBin.gz', 'optbin-gzip')]
    ids = ['afiro', 'AUG2D', 'AUG3DCQP', 'QPLIB_2676', 'nql30', 'nb_L2',
           'LogExpCR-n20-m400', 'infeas_clean_10_10_1']
    selected = {x['id']: x for x in small['results']}
    table = [r'\begin{table}[ht]\centering\small\setlength{\tabcolsep}{3pt}',
             r'\begin{tabular}{lrrrrrrrr}\toprule',
             r' & \multicolumn{2}{c}{Text.gz} & \multicolumn{2}{c}{raw.h5.gz} & '
             r'\multicolumn{2}{c}{OptBin} & \multicolumn{2}{c}{OptBin.gz} \\',
             r'Model & KiB & ms & KiB & ms & KiB & ms & KiB & ms \\ \midrule']
    for id in ids:
        row = {x['format']: x for x in selected[id]['formats']}
        cells = [escape(id)]
        for _,key in layouts:
            cells += [f"{row[key]['bytes']/1024:.2f}", f"{row[key]['median_seconds']*1000:.3f}"]
        table.append(' & '.join(cells) + r' \\')
    table += [r'\bottomrule\end{tabular}',
        r'\caption{Stored KiB ($2^{10}$ bytes), including metadata, and complete loading medians in milliseconds '
        r'(seven shuffled fresh-process trials per format). Text.gz is MPS.gz read by Gurobi in the first four rows '
        r'and original CBLIB CBF.gz read by MOSEK in the last four. '
        r'Decompression and binary temporary-file staging are included.}\end{table}']
    text_ratios=[]; h5_ratios=[]; mps_ratios=[]; cbf_ratios=[]; mps_gzip_ratios=[];cbf_gzip_ratios=[];larger=0; larger_gzip=0
    for model in small['results']:
        row={x['format']:x for x in model['formats']}
        text_ratios.append(row['text-gzip']['median_seconds']/row['optbin']['median_seconds'])
        h5_ratios.append(row['raw-hdf5-gzip']['median_seconds']/row['optbin']['median_seconds'])
        (mps_ratios if model['source_format']=='mps' else cbf_ratios).append(text_ratios[-1])
        if model['source_format']=='mps':mps_gzip_ratios.append(row['text-gzip']['median_seconds']/row['optbin-gzip']['median_seconds'])
        else:cbf_gzip_ratios.append(row['text-gzip']['median_seconds']/row['optbin-gzip']['median_seconds'])
        larger += row['optbin']['bytes'] > row['text-gzip']['bytes']
        larger_gzip += row['optbin-gzip']['bytes'] > row['text-gzip']['bytes']
    regions['GZIP_RESULTS'] = (
        f"All {len(small['results'])} real continuous models pass independent source-model comparisons; "
        'the three binary representations reconstruct identical numerical arrays. '
        'The table shows eight examples spanning LP, QP, QCQP, SOCP, exponential cones and PSD data, measured on '
        + escape(small['environment']['cpu']) + '.\n\n' + '\n'.join(table) +
        '\n\nOn the 13 MPS models, default OptBin loads '
        f'{min(mps_ratios):.2f}--{max(mps_ratios):.2f} times as fast as public gurobipy.read; '
        f'OptBin.gz loads {min(mps_gzip_ratios):.2f}--{max(mps_gzip_ratios):.2f} times as fast. '
        f'On the nine CBF cases, default OptBin loads {min(cbf_ratios):.2f}--{max(cbf_ratios):.2f} times as fast as MOSEK Task.readdata; '
        f'OptBin.gz loads {min(cbf_gzip_ratios):.2f}--{max(cbf_gzip_ratios):.2f} times as fast. '
        'Across all 22 cases it loads '
        f'{min(h5_ratios):.2f}--{max(h5_ratios):.2f} times as fast as the raw.h5.gz temporary-file path. '
        'The highly compressible AUG cases are favorable examples. '
        f'Default OptBin is larger than Text.gz on {larger} of 22 models; OptBin.gz is larger on {larger_gzip}, '
        'afiro and the tiny clean PSD case. '
        'Outer gzip sometimes reduces OptBin metadata substantially, but also adds a decompression/staging step. '
        'Small-case results include reader initialization and metadata costs and are not large-model speedup claims.')
    models = [{x['format']: x for x in m['formats']} for m in large['results']]
    creation = [m['preparation'] for m in large['results']]
    raw_hdf5_bytes = [p['raw_hdf5_gzip']['input_bytes'] for p in creation]
    for model, prep, raw_bytes in zip(models, creation, raw_hdf5_bytes):
        assert raw_bytes > 0
        assert prep['raw_hdf5_gzip']['output_bytes'] == model['raw-hdf5-gzip']['bytes']
        assert prep['optbin_gzip']['input_bytes'] == model['optbin']['bytes']
    large_layouts = [('MPS.gz (Gurobi)', 'text-gzip'),
                     ('raw.h5 (uncompressed)', None), *layouts[1:]]
    table = [r'\begin{table}[ht]\centering\small',r'\begin{tabular}{lrrrr}\toprule',
        r'Representation & prod GiB & prod seconds & mcf GiB & mcf seconds \\ \midrule']
    for label,key in large_layouts:
        cells = [label]
        for model,raw_bytes in zip(models, raw_hdf5_bytes):
            cells += ([f'{raw_bytes/2**30:.3f}', '--'] if key is None else
                      [f"{model[key]['bytes']/2**30:.3f}",
                       f"{model[key]['median_seconds']:.2f}" if model[key]['runs'] else '--'])
        table.append(' & '.join(cells) + r' \\')
    table += [r'\bottomrule\end{tabular}',
        r'\caption{Large LP storage and complete loading. Times are medians of three shuffled fresh-process trials. '
        r'Raw .h5 has storage measurements only. '
        r'prod is \texttt{' + escape(large['results'][0]['id']) + r'}; mcf is \texttt{' +
        escape(large['results'][1]['id']) + r'}. GiB means $2^{30}$ bytes. Gurobi returns its solver model; the binary paths return validated numerical arrays.}\end{table}']
    rss = [x['optbin']['median_peak_rss_bytes']/2**30 for x in models]
    gurobi_rss=[x['text-gzip']['median_peak_rss_bytes']/2**30 for x in models]
    assert all(max(x['median_peak_rss_bytes'] for k,x in m.items() if k!='text-gzip')-
               min(x['median_peak_rss_bytes'] for k,x in m.items() if k!='text-gzip') < .01*2**30
               for m in models)
    raw_hdf5_ratios = [raw_bytes/model['optbin']['bytes']
                      for model,raw_bytes in zip(models, raw_hdf5_bytes)]
    raw_storage_note = (
        'Before gzip, raw HDF5 occupies ' +
        ' and '.join(f'{b/2**30:.2f}' for b in raw_hdf5_bytes) +
        ' GiB (prod/mcf), ' + ' and '.join(f'{r:.1f}' for r in raw_hdf5_ratios) +
        ' times default OptBin. The tested raw.h5.gz loader needs a temporary file of that expanded size; '
        'default OptBin reads its internally compressed file directly. These savings concern stored files '
        'and temporary disk space; decoded RAM is essentially unchanged. HDF5 also supports internal '
        'dataset compression, which this raw-array baseline does not use.')
    size_tradeoffs=''
    for original,model in zip(large['results'],models):
        if model['raw-hdf5-gzip']['bytes'] < min(model['optbin']['bytes'],model['optbin-gzip']['bytes']):
            size_tradeoffs += (r'For \texttt{' + escape(original['id']) +
                '}, raw.h5.gz is smaller than both OptBin representations; '
                'stored size and loading time favor different choices. ')
    shapes=[]
    for label,model in zip(['prod','mcf'],large['results']):
        v=next(x for x in model['verification'] if x['format']=='gurobi-mps-gzip')
        shapes.append(f"{label} has {v['rows']:,} rows, {v['cols']:,} columns and {v['nnz']:,} matrix nonzeros")
    regions['GZIP_LARGE_RESULTS'] = (
        'Gurobi reads both original large MPS.gz files; complete public-API LP comparisons match their OptBin numerical models. '
        'A separate post-timing metadata audit confirms continuous single-objective LP profiles, unchanged file hashes and Gurobi fingerprints. '
        'The binary representations also pass decoded-array byte comparisons. These files retain previously migrated BQP data. '
        'The shared host uses ' + escape(large['environment']['cpu']) + '. '
        + '; '.join(shapes) + '. '
        'Two case jobs run concurrently; each loads one format at a time in shuffled rounds. '
        'The prod gzip files use GNU gzip with one compression thread; mcf uses pigz 2.8 with 16 compression threads, '
        'both at level 6. Native decoding is serial; Gurobi uses Threads=1. '
        'Environment startup and reference extraction are excluded from Gurobi read time.\n\n' +
        '\n'.join(table) + '\n\n' + raw_storage_note + '\n\n' + size_tradeoffs +
        'Default OptBin loads ' + ' and '.join(f"{x['text-gzip']['median_seconds']/x['optbin']['median_seconds']:.2f}" for x in models) +
        ' times as fast as Gurobi on these cases; OptBin.gz loads ' +
        ' and '.join(f"{x['text-gzip']['median_seconds']/x['optbin-gzip']['median_seconds']:.2f}" for x in models) +
        ' times as fast. '
        'Creating raw.h5.gz from the existing raw HDF5 files took '
        f"{creation[0]['raw_hdf5_gzip']['seconds']:.1f} and {creation[1]['raw_hdf5_gzip']['seconds']:.1f} seconds; "
        'creating OptBin.gz from the existing OptBin files took '
        f"{creation[0]['optbin_gzip']['seconds']:.1f} and {creation[1]['optbin_gzip']['seconds']:.1f} seconds. "
        'These are gzip-only preparation costs, not source-to-binary conversion times.\n\n' +
        f'OptBin peak process RSS is {rss[0]:.2f} and {rss[1]:.2f} GiB; the other binary paths are within 0.01 GiB. '
        f'Gurobi read-phase RSS is {gurobi_rss[0]:.2f} and {gurobi_rss[1]:.2f} GiB. '
        'The latter includes the vendor model and its metadata, so this is not a pure container-memory comparison. '
        'Expanded numeric arrays dominate memory. '
        'RSS excludes filesystem page cache, expanded gzip temporaries and later solver allocations. '
        'Full verification temporarily holds additional model and array copies. '
        'Binary gzip staging needs temporary space equal to the uncompressed file size.'
        '\n\n'
        'The prod matrix contains 2,310,559,600 entries. Gurobi reads it successfully; in 13.0.3, '
        r'\texttt{getA()} then fails while querying the legacy 32-bit nonzero count. '
        'Full verification uses public row-only model copies below the query limit, with complete row/nonzero coverage. '
        'This extraction work is outside read timing and its RSS snapshot.')
    path = ROOT / 'docs/technical-report.tex'; text = path.read_text()
    for key, value in regions.items():
        pattern = r'(% BEGIN AUTO: ' + key + r'\n).*?(% END AUTO: ' + key + r')'
        text, count = re.subn(pattern, lambda m: m[1] + value + '\n' + m[2], text, flags=re.S)
        if count != 1:
            raise ValueError('missing or duplicate technical-report region: ' + key)
    path.write_text(text)
    evidence = {'tool_version': version, 'purpose': 'Practical tool technical report',
                'report_sha256': sources, 'regions': list(regions),
                'large_raw_hdf5_storage': [
                    {'id': case['id'], 'raw_hdf5_bytes': raw_bytes,
                     'raw_hdf5_gzip_bytes': model['raw-hdf5-gzip']['bytes'],
                     'optbin_bytes': model['optbin']['bytes'],
                     'raw_hdf5_over_optbin': ratio,
                     'source_field': 'preparation.raw_hdf5_gzip.input_bytes'}
                    for case,model,raw_bytes,ratio in
                    zip(large['results'], models, raw_hdf5_bytes, raw_hdf5_ratios)],
                'scope': 'Archived development measurements; release packaging is verified separately'}
    (ROOT / 'docs/report-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    full_table = ['| Model | Source reader | Text.gz KiB / ms | raw.h5.gz KiB / ms | OptBin KiB / ms | OptBin.gz KiB / ms |',
                  '|---|---|---:|---:|---:|---:|']
    for model in small['results']:
        row={x['format']:x for x in model['formats']}
        cells=[model['id'],'Gurobi MPS' if model['source_format']=='mps' else 'MOSEK CBF']
        for _,key in layouts:
            cells.append(f"{row[key]['bytes']/1024:.2f} / {1000*row[key]['median_seconds']:.3f}")
        full_table.append('| ' + ' | '.join(cells) + ' |')
    full_table.append(f'\nDefault OptBin is larger than Text.gz on {larger}/22 models; OptBin.gz is larger on {larger_gzip}/22. '
                      'The table retains these cases. Times are medians of seven trials, including decompression.')
    detail=ROOT/'docs/gzip-comparison.md'; contents=detail.read_text()
    contents,n=re.subn(r'(<!-- BEGIN RESULTS -->\n).*?(<!-- END RESULTS -->)',
        lambda m:m[1]+'\n'.join(full_table)+'\n'+m[2],contents,flags=re.S)
    if n!=1:raise ValueError('missing or duplicate gzip comparison results region')
    large_table=['| Model | Rows | Variables | A nonzeros |','|---|---:|---:|---:|']
    for m in large['results']:
        v=next(x for x in m['verification'] if x['format']=='gurobi-mps-gzip')
        large_table.append(f"| {m['id']} | {v['rows']:,} | {v['cols']:,} | {v['nnz']:,} |")
    large_table+=['','| Representation | prod GiB / seconds | mcf GiB / seconds |',
        '|---|---:|---:|']
    for label,key in large_layouts:
        cells=[label]+[f'{raw_bytes/2**30:.3f} / --' if key is None else
            f"{m[key]['bytes']/2**30:.3f} / {m[key]['median_seconds']:.2f}"
            for m,raw_bytes in zip(models, raw_hdf5_bytes)]
        large_table.append('| '+' | '.join(cells)+' |')
    large_table+=['', 'Times are medians of three shuffled fresh-process trials, including gzip decompression. '
        'Every large source LP passes complete independent Gurobi-to-OptBin numerical comparison; '
        'binary paths pass decoded-array byte comparisons. Default OptBin loads '+
        ' and '.join(f"{m['text-gzip']['median_seconds']/m['optbin']['median_seconds']:.2f}×" for m in models)+
        ' as fast as Gurobi on prod and mcf, respectively. '
        'Gurobi returns its solver model with metadata; the binary readers return validated arrays. '
        'Raw .h5 has storage measurements only.', '', raw_storage_note, '',
        f'Gurobi read-phase peak RSS is {gurobi_rss[0]:.2f} / {gurobi_rss[1]:.2f} GiB (prod / mcf). '
        f'OptBin is {rss[0]:.2f} / {rss[1]:.2f} GiB; the other binary paths are within 0.01 GiB. '
        'Extraction, reference loading and later solver allocations are outside these snapshots. '
        'The representations and metadata differ, so this is not an isolated container-memory comparison.']
    contents,n=re.subn(r'(<!-- BEGIN LARGE RESULTS -->\n).*?(<!-- END LARGE RESULTS -->)',
        lambda m:m[1]+'\n'.join(large_table)+'\n'+m[2],contents,flags=re.S)
    if n!=1:raise ValueError('missing or duplicate large gzip comparison results region')
    detail.write_text(contents)
    if args.update_only:
        print(path); return
    engine = shutil.which('pdflatex')
    if not engine:
        raise RuntimeError('pdflatex unavailable; report source and evidence are preserved')
    output = ROOT / 'output/pdf'; output.mkdir(parents=True, exist_ok=True)
    for iteration in range(2):
        result = subprocess.run([engine, '-interaction=nonstopmode', '-halt-on-error', '-file-line-error',
            '-output-directory=' + str(output), '-jobname=optbin-technical-report', str(path)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (output / f'technical-report-pass{iteration+1}.txt').write_text(result.stdout)
        if result.returncode:
            raise RuntimeError(result.stdout[-4000:])
    log = (output / 'optbin-technical-report.log').read_text(errors='replace')
    if re.search(r'Overfull \\[hv]box|undefined references|Citation .* undefined', log):
        raise RuntimeError('Overfull boxes or unresolved references in technical report')
    pdf = output / 'optbin-technical-report.pdf'
    if args.render:
        renderer = shutil.which('pdftoppm')
        if not renderer:
            raise RuntimeError('pdftoppm unavailable for visual review')
        qa = ROOT / 'tmp/pdfs'; qa.mkdir(parents=True, exist_ok=True)
        subprocess.run([renderer, '-r', '110', '-png', str(pdf), str(qa / 'technical-report')], check=True)
    print(pdf)


if __name__ == '__main__':
    main()
