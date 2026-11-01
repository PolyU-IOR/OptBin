#!/usr/bin/env python3
"""Summarize completed native reports without mixing historical timing scopes."""
import hashlib
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[1]


def main():
    sources={}
    def read(name):
        p=ROOT/'benchmarks'/name;data=p.read_bytes();sources[name]=hashlib.sha256(data).hexdigest();return json.loads(data)
    small,large,inputs=read('native-cpu.json'),read('native-large.json'),read('large-inputs.json')
    boundary=read('large-boundary.json')
    if len(small['results'])!=6 or len(large['results'])!=2:raise ValueError('expected complete six-model and two-model studies')
    if not (boundary['comparison']['bitwise_reference_equal'] and boundary['nnz_A']>2**32 and boundary['row_index_bytes']==4):
        raise ValueError('large entry-count boundary not verified')
    if len(inputs['instances'])!=8 or not all(x['payload_sha256_verified_this_run'] and
            x['observed_payload_sha256']==x['bqp_sha256_from_existing_manifest'] for x in inputs['instances']):
        raise ValueError('eight source payload integrity checks not verified')
    summary={'scope':'Measured native C warm-cache implementations; no source-text/GPU/solver speedup claim',
        'sources_sha256':sources,'results':[],
        'additional_correctness':{'beyond_u32_entry_count_instance':boundary['id'],
            'nnz_A':boundary['nnz_A'],'row_index_bytes':4,'bitwise_reference_equal':True,
            'source_bqp_payload_sha256_matches':8}}
    for study in [small,large]:
        for row in study['results']:
            formats={x['format']:x for x in row['formats']};base=formats['optbin']
            if not all(x['bitwise_reference_equal'] for x in row['verification']):raise ValueError('byte comparison missing')
            sha=row['numerical_sha256']
            result={'id':row['id'],'large':study is large,'decoded_bytes':row['verification'][0]['decoded_bytes'],
                    'numerical_sha256':sha,'bitwise_reference_equal':True,'formats':{}}
            for name,value in formats.items():
                runs=value['runs'];times=[x['seconds'] for x in runs]
                if any(x['numerical_sha256']!=sha or x['bytes']!=value['bytes'] for x in runs):raise ValueError('file/model changed')
                result['formats'][name]={'bytes':value['bytes'],'trials':len(runs),'median_seconds':statistics.median(times),
                    'min_seconds':min(times),'max_seconds':max(times),'median_peak_rss_bytes':value['median_peak_rss_bytes'],
                    'time_over_optbin':statistics.median(times)/base['median_seconds'],
                    'bytes_over_optbin':value['bytes']/base['bytes'],'input_blocks':[x['input_blocks'] for x in runs]}
            a,b=result['formats']['optbin'],result['formats']['hdf5-encoded']
            result['same_codec_observed_ranges_overlap']=max(a['min_seconds'],b['min_seconds'])<=min(a['max_seconds'],b['max_seconds'])
            summary['results'].append(result)
    (ROOT/'benchmarks/native-analysis.json').write_text(json.dumps(summary,indent=2)+'\n')
    labels=['optbin','hdf5-encoded','hdf5-raw','hdf5-gzip']
    lines=['## Native C evaluation, 27 October 2026','',
        'The matched native experiment supersedes Python/frontend timing comparisons for the container question. '
        'Reports retain exact comparisons, all trials, RSS snapshots and scope limits. '
        'Both encoded paths share parsing, allocation, block decoding, decoded CRCs and full validation.','',
        'Large measurements use the Xeon server and three shuffled fresh-process trial rounds; '
        'smaller measurements use the i9 workstation and seven rounds. Files are warm. '
        'Hashing and explicit byte comparisons are outside the timer and RSS snapshot.','',
        '| Model | OptBin GiB | H5 same codecs GiB | H5 raw GiB | H5 shuffle/gzip GiB | Decoded GiB |',
        '|---|---:|---:|---:|---:|---:|']
    for row in summary['results']:
        if row['large']:lines.append('| '+row['id']+' | '+' | '.join(f'{row["formats"][n]["bytes"]/2**30:.3f}' for n in labels)+f' | {row["decoded_bytes"]/2**30:.2f} |')
    lines+=['','| Model | OptBin seconds | H5 same codecs seconds | H5 raw seconds | H5 shuffle/gzip seconds |',
            '|---|---:|---:|---:|---:|']
    for row in summary['results']:
        if row['large']:lines.append('| '+row['id']+' | '+' | '.join(f'{row["formats"][n]["median_seconds"]:.2f}' for n in labels)+' |')
    lines+=['','Large-case details:','']
    for row in summary['results']:
        if not row['large']:continue
        v=row['formats'];lines.append(f'- `{row["id"]}`: raw HDF5 takes {v["hdf5-raw"]["bytes_over_optbin"]:.2f}x the file space. '
            f'The same-codec HDF5/OptBin median-time ratio is {v["hdf5-encoded"]["time_over_optbin"]:.4f}; '
            f'OptBin peak RSS is {v["optbin"]["median_peak_rss_bytes"]/2**30:.2f} GiB. '
            f'Observed timing ranges overlap: {str(row["same_codec_observed_ranges_overlap"]).lower()}.')
    lines+=['','Interpretation:','',
        '- Identical encodings can be reused inside HDF5. These measurements do not establish universal custom-container superiority.',
        '- Warm raw-array loading can be faster despite much larger files. Compressed size and CPU loading cost are different objectives.',
        '- Process memory is dominated by identical decoded arrays. RSS excludes filesystem page cache and later solver/adapter allocations.',
        '- Historical MPS/HPR-LP timings include different model-construction work and are not divided by these native timings.',
        '- The extra legacy-BQP compatibility timings include two validation layers and are supplementary, not a clean container comparison.',
        '- The shuffle/gzip comparison changes the compressor and encodings together. Large-instance matched-compressor ablations remain pending.',
        '- The v2 files were migrated from existing BQP blocks. This is not a new large-MPS converter timing.',
        '', 'Separately, `prod_200_500_02` passes a full native v2 load and byte comparison with its BQP source '
        'at 4,443,349,500 stored entries, using 32-bit row indices and 64-bit pointers. This is a correctness check, '
        'not a repeated performance measurement. All eight source BQP payload SHA-256 hashes match their existing manifest; '
        'the integrity pass ran after the timing experiments.',
        '', '[Native methodology](../docs/native-hdf5.md), [raw small report](native-cpu.json), '
        '[raw large report](native-large.json), [derived analysis](native-analysis.json), '
        '[input provenance](large-inputs.json), [boundary correctness](large-boundary.json), '
        '[technical note](../docs/optbin-technical-report.pdf).']
    p=ROOT/'benchmarks/evaluation.md';text=p.read_text();begin='<!-- BEGIN NATIVE_EVALUATION -->';end='<!-- END NATIVE_EVALUATION -->'
    body=begin+'\n'+'\n'.join(lines)+'\n'+end
    if begin in text:text=text[:text.index(begin)]+body+text[text.index(end)+len(end):]
    else:text=text.rstrip()+'\n\n'+body+'\n'
    p.write_text(text);print(ROOT/'benchmarks/native-analysis.json')


if __name__=='__main__':main()
