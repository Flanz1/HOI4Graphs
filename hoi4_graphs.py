#!/usr/bin/env python3
"""
HOI4 IC Graph Tool
==================
Point it at a folder of HOI4 saves (e.g. a multiplayer game's monthly autosaves) and it
produces the full set of graphs:

  Military / Naval IC : monthly + cumulative per nation, Axis vs Allies (monthly + cumulative),
                        minor powers (monthly + cumulative), sub-faction analysis
  Civilian IC         : monthly + cumulative per nation, Axis vs Allies (monthly + cumulative)
  Army manpower       : per nation, Axis vs Allies
  Division cost       : per nation, Axis vs Allies   (equipment in fielded divisions x IC cost)
  Deployed air IC     : per nation, Axis vs Allies   (equipment in air wings x IC cost)

The plotting code is ported from the original "bbb/zzz/mmm graph maker" scripts so the
images look the same; the data now comes straight from binary saves instead of the
plain-text regex passes.

Usage:
    python hoi4_graphs.py "C:\\path\\to\\save folder"            # graphs go to <folder>\\graphs
    python hoi4_graphs.py saves -o out --config my_game.json
    python hoi4_graphs.py                                         # opens a folder picker
"""
import argparse
import csv
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hoi4_save_parser as parser  # noqa: E402

CACHE_VERSION = 5


# =====================================================================================
# Config
# =====================================================================================
def load_config(path):
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    cfg.setdefault('nations', [])
    cfg.setdefault('factions', {'Axis': [], 'Allies': []})
    cfg.setdefault('sub_factions', [])
    cfg.setdefault('minor_countries', [])
    cfg.setdefault('colors', {})
    cfg.setdefault('default_division_equipment_cost', 1.1)
    cfg.setdefault('default_air_equipment_cost', 23)
    cfg.setdefault('manpower_mode', 'original')
    cfg.setdefault('monthly_multiplier', 30)
    return cfg


# =====================================================================================
# Save scanning (parallel + cached)
# =====================================================================================
def find_saves(folder):
    out = []
    for name in os.listdir(folder):
        if name.lower().endswith('.hoi4'):
            out.append(os.path.join(folder, name))
    return out


def _cache_path(cache_dir, path):
    st = os.stat(path)
    key = f"{os.path.basename(path)}.{st.st_size}.{int(st.st_mtime)}.v{CACHE_VERSION}.json"
    return os.path.join(cache_dir, key)


def _extract_job(path, cache_file):
    if cache_file and os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            return path, json.load(f), True
    rec = parser.extract(path)
    if cache_file:
        tmp = cache_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(rec, f)
        os.replace(tmp, cache_file)
    return path, rec, False


def scan_saves(paths, cache_dir, jobs):
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    results, errors = [], []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(_extract_job, p, _cache_path(cache_dir, p) if cache_dir else None): p
                for p in paths}
        done = 0
        for fut in as_completed(futs):
            p = futs[fut]
            done += 1
            try:
                _, rec, cached = fut.result()
                results.append(rec)
                tag = 'cached' if cached else 'parsed'
                print(f"  [{done}/{len(paths)}] {tag:6s} {os.path.basename(p)}  ({rec['date'][:10] if rec['date'] else '?'})")
            except Exception as e:  # keep going on bad files
                errors.append((p, e))
                print(f"  [{done}/{len(paths)}] FAILED {os.path.basename(p)}: {e}")
    print(f"Scanned {len(results)} saves in {time.time() - t0:.1f}s")
    return results, errors


# =====================================================================================
# Build per-nation time series from the extracted saves
# =====================================================================================
def build_series(records, cfg):
    nations = cfg['nations']
    mult = cfg['monthly_multiplier']

    # one record per in-game date, chronological (duplicates of the same date: keep one)
    by_date = {}
    for r in records:
        if r['date']:
            by_date[r['date']] = r
    records = [by_date[d] for d in sorted(by_date)]
    dates = [datetime.fromisoformat(r['date']) for r in records]

    # --- equipment cost library: first cost seen wins (chronological, nation order) ----
    cost_lib = {}
    order = {n: i for i, n in enumerate(nations)}
    for r in records:
        for tag, eq, cost in sorted(r['line_costs'], key=lambda x: order.get(x[0], 1e9)):
            if tag in order and eq not in cost_lib:
                cost_lib[eq] = cost

    div_default = cfg['default_division_equipment_cost']
    air_default = cfg['default_air_equipment_cost']
    missing = {'division': set(), 'air': set()}

    def eq_cost(eq_list, default, kind):
        total = 0.0
        for eq, amount in eq_list:
            c = cost_lib.get(eq)
            if c is None:
                c = default
                missing[kind].add(eq)
            total += c * amount
        return total

    series = {k: {n: [] for n in nations} for k in
              ('mil', 'nav', 'civ', 'manpower', 'division_cost', 'air_cost')}
    nation_set = set(nations)
    for r in records:
        cs = r['countries']
        div_cost = {n: 0.0 for n in nations}
        for owner, eqs in r['divisions']:
            if owner in nation_set:
                div_cost[owner] += eq_cost(eqs, div_default, 'division')
        air_cost = {n: 0.0 for n in nations}
        for tag, eqs in r['air_wings']:
            if tag in nation_set:
                air_cost[tag] += eq_cost(eqs, air_default, 'air')
        for n in nations:
            c = cs.get(n, {})
            series['mil'][n].append(c.get('mil', 0.0) * mult)
            series['nav'][n].append(c.get('nav', 0.0) * mult)
            series['civ'][n].append(c.get('civ', 0.0) * mult)
            mp = c.get('field_manpower', 0)
            if cfg['manpower_mode'] == 'original':
                # Reproduces the original regex script: if the country has divisions in
                # training, their manpower is subtracted; otherwise trained-but-undeployed
                # divisions in the queue are counted as fielded.
                tr = c.get('queue_training_manpower', 0)
                mp = mp - tr if tr > 0 else mp + c.get('queue_ready_manpower', 0)
            series['manpower'][n].append(max(0, mp))
            series['division_cost'][n].append(div_cost[n])
            series['air_cost'][n].append(air_cost[n])
    info = {'cost_library_size': len(cost_lib),
            'missing_division_costs': len(missing['division']),
            'missing_air_costs': len(missing['air'])}
    return dates, series, info


def to_country_data(dates, per_nation, skip_zero=False):
    """{tag: {'dates': [...], 'values': [...]}} - the structure the original graph code used."""
    out = {}
    for tag, vals in per_nation.items():
        d, v = [], []
        for dt, x in zip(dates, vals):
            if skip_zero and not x > 0:
                continue
            d.append(dt)
            v.append(x)
        out[tag] = {'dates': d, 'values': v}
    return out


# =====================================================================================
# Plotting  (ported from the original bbb / zzz / mmm graph makers)
# =====================================================================================
def format_x_axis(country_data):
    all_dates = []
    for data in country_data.values():
        all_dates.extend(data['dates'])
    if all_dates:
        all_dates.sort()
        date_range = (max(all_dates) - min(all_dates)).days
        ax = plt.gca()
        if date_range <= 30:
            ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, date_range // 10)))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        elif date_range <= 365:
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        else:
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
        plt.xticks(rotation=45)


def _save(path):
    plt.tight_layout()
    plt.savefig(path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  wrote {os.path.basename(path)}")


def faction_sums(country_data, members_by_faction):
    """Sum country series into faction series (same as calculate_monthly_faction_data)."""
    all_dates = sorted({d for data in country_data.values() for d in data['dates']})
    lookup = {c: dict(zip(data['dates'], data['values'])) for c, data in country_data.items()}
    out = {}
    for faction, members in members_by_faction.items():
        vals = []
        for dt in all_dates:
            vals.append(sum(lookup[c].get(dt, 0) for c in members if c in lookup))
        out[faction] = {'dates': list(all_dates), 'values': vals}
    return out


def cumulative(country_data):
    out = {}
    for c, data in country_data.items():
        if not data['dates']:
            continue
        pairs = sorted(zip(data['dates'], data['values']), key=lambda x: x[0])
        run, vals = 0, []
        for _, v in pairs:
            run += v
            vals.append(run)
        out[c] = {'dates': [p[0] for p in pairs], 'values': vals}
    return out


def fmt(v, comma):
    return f'{v:,.0f}' if comma else f'{v:.0f}'


def plot_individual(country_data, colors, title, ylabel, path, *, legend_fs=8, markersize=3,
                    comma=False):
    if not country_data:
        return
    plt.figure(figsize=(15, 10))
    ranked = sorted(((c, max(d['values'])) for c, d in country_data.items() if d['values']),
                    key=lambda x: x[1], reverse=True)
    for c, mx in ranked:
        d = country_data[c]
        plt.plot(d['dates'], d['values'], marker='o', linewidth=2, markersize=markersize,
                 color=colors.get(c, '#000000'), label=f'{c} (Max: {fmt(mx, comma)})')
    plt.title(title, fontsize=16, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel(ylabel, fontsize=12)
    format_x_axis(country_data)
    plt.grid(True, alpha=0.3)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=legend_fs)
    _save(path)


def plot_cumulative(cum_data, colors, title, ylabel, path, *, legend_fs=8):
    if not cum_data:
        return
    plt.figure(figsize=(15, 10))
    ranked = sorted(((c, d['values'][-1]) for c, d in cum_data.items() if d['values']),
                    key=lambda x: x[1], reverse=True)
    for c, final in ranked:
        d = cum_data[c]
        plt.plot(d['dates'], d['values'], linewidth=2.5, color=colors.get(c, '#000000'),
                 label=f'{c} (Total: {final:.0f})')
    plt.title(title, fontsize=16, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel(ylabel, fontsize=12)
    format_x_axis(cum_data)
    plt.grid(True, alpha=0.3)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=legend_fs)
    _save(path)


def plot_axis_vs_allies(fdata, title, ylabel, stats_title, path, *, label_kind='Max',
                        use_final=False, comma=False):
    """Monthly or cumulative Axis vs Allies area graph with the stats box."""
    if not fdata:
        return
    plt.figure(figsize=(12, 8))
    styles = (('Axis', 'Axis Powers', '#8B0000', 'o'), ('Allies', 'Allied Powers', '#000080', 's'))
    for key, name, color, marker in styles:
        d = fdata.get(key)
        if d and d['dates']:
            v = d['values'][-1] if label_kind == 'Total' else max(d['values'])
            plt.plot(d['dates'], d['values'], linewidth=3, color=color, marker=marker, markersize=4,
                     label=f'{name} ({label_kind}: {fmt(v, comma)})')
    for key, _, color, _ in styles:
        d = fdata.get(key)
        if d and d['dates']:
            plt.fill_between(d['dates'], d['values'], alpha=0.2, color=color)
    plt.title(title, fontsize=16, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel(ylabel, fontsize=12)
    format_x_axis(fdata)
    plt.grid(True, alpha=0.3)
    plt.legend(loc='upper left', fontsize=12)
    ax_v, al_v = fdata['Axis']['values'], fdata['Allies']['values']
    if ax_v and al_v:
        if use_final:
            a, b = ax_v[-1], al_v[-1]
        else:
            a, b = sum(ax_v) / len(ax_v), sum(al_v) / len(al_v)
        text = f'{stats_title}\nAxis: {fmt(a, comma)}\nAllies: {fmt(b, comma)}'
        text += (f'\nAxis leads by: {fmt(a - b, comma)}' if a > b
                 else f'\nAllies lead by: {fmt(b - a, comma)}')
        plt.text(0.02, 0.98, text, transform=plt.gca().transAxes, verticalalignment='top',
                 bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    _save(path)


def plot_sub_factions(country_data, sub_factions, title, ylabel, stats_title, path):
    if not sub_factions:
        return
    data = faction_sums(country_data, {s['name']: s['members'] for s in sub_factions})
    plt.figure(figsize=(14, 10))
    for s in sub_factions:
        d = data[s['name']]
        if not d['dates']:
            continue
        mx = max(d['values']) if d['values'] else 0
        plt.plot(d['dates'], d['values'], linewidth=3, color=s.get('color', '#000000'),
                 marker=s.get('marker', 'o'), markersize=5, label=f"{s['name']} (Max: {mx:.0f})")
        plt.fill_between(d['dates'], d['values'], alpha=0.2, color=s.get('color', '#000000'))
    plt.title(title, fontsize=16, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel(ylabel, fontsize=12)
    format_x_axis(data)
    plt.grid(True, alpha=0.3)
    plt.legend(loc='upper left', fontsize=10)
    text = stats_title + '\n'
    best, best_name = 0, ''
    for s in sub_factions:
        v = data[s['name']]['values']
        if v:
            avg = sum(v) / len(v)
            text += f"{s['name']}: {avg:.0f}\n"
            if avg > best:
                best, best_name = avg, s['name']
    if best_name:
        text += f'\nLeader: {best_name}'
    plt.text(0.02, 0.98, text, transform=plt.gca().transAxes, verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='white', alpha=0.9), fontsize=9)
    _save(path)


def plot_nation_timeline(dates, per_nation, nation_faction, colors, title, ylabel, path):
    labels = [d.strftime('%Y.%m.%d') for d in dates]
    axis_n = sorted(n for n in per_nation if nation_faction.get(n) == 'Axis')
    allied_n = sorted(n for n in per_nation if nation_faction.get(n) == 'Allies')
    plt.figure(figsize=(16, 10))
    for n in axis_n + allied_n:
        vals = per_nation[n]
        if max(vals, default=0) > 0:
            fac = nation_faction[n]
            plt.plot(range(len(labels)), vals, color=colors.get(n, '#808080'), marker='o',
                     linewidth=2.5, label=f'{n} ({fac})', markersize=5,
                     linestyle='-' if fac == 'Axis' else '--')
    plt.title(title, fontsize=18, fontweight='bold')
    plt.xlabel('Game Date', fontsize=14)
    plt.ylabel(ylabel, fontsize=14)
    plt.xticks(range(len(labels)), labels, rotation=45, ha='right')
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=10)
    plt.grid(True, alpha=0.3, linestyle=':', linewidth=0.5)
    _save(path)


def plot_faction_timeline(dates, per_nation, factions, title, ylabel, path):
    labels = [d.strftime('%Y.%m.%d') for d in dates]
    sums = {f: [sum(per_nation[n][i] for n in factions.get(f, []) if n in per_nation)
                for i in range(len(labels))] for f in ('Axis', 'Allies')}
    plt.figure(figsize=(14, 8))
    plt.plot(range(len(labels)), sums['Axis'], color='#8B0000', marker='s', linewidth=3,
             label='Axis Powers', markersize=6, linestyle='-')
    plt.plot(range(len(labels)), sums['Allies'], color='#000080', marker='^', linewidth=3,
             label='Allied Powers', markersize=6, linestyle='--')
    plt.title(title, fontsize=18, fontweight='bold')
    plt.xlabel('Game Date', fontsize=14)
    plt.ylabel(ylabel, fontsize=14)
    plt.xticks(range(len(labels)), labels, rotation=45, ha='right')
    plt.legend(fontsize=12)
    plt.grid(True, alpha=0.3)
    _save(path)


# =====================================================================================
# All graphs
# =====================================================================================
IC_TEXT = {
    'mil': dict(
        prefix='military', short='MIL', long='Military',
        individual='Monthly MIL IC', ylabel='Military Industrial Capacity (IC)',
        cum_title='Cumulative Military Industrial Capacity Over Time by Nation',
        cum_ylabel='Cumulative Military Industrial Capacity (IC)',
        fac_title='Axis vs Allies: Monthly MIL IC',
        fac_ylabel='Monthly Total Military Industrial Capacity (IC)',
        fac_stats='Average Monthly Military IC:',
        cfac_title='Axis vs Allies : Cumulative Produced MIL IC',
        cfac_ylabel='Cumulative Total Military Industrial Capacity (IC)',
        cfac_stats='Final Cumulative Military IC:',
        minor='Minor Monthly MIL IC', minor_cum='Minor Powers : Cumulative Produced MIL IC',
        sub='Sub-Faction Analysis: Monthly MIL IC'),
    'nav': dict(
        prefix='navy', short='NAV', long='Naval',
        individual='Monthly NAV IC', ylabel='Naval Industrial Capacity (IC)',
        cum_title='Cumulative Naval Industrial Capacity Over Time by Nation',
        cum_ylabel='Cumulative Naval Industrial Capacity (IC)',
        fac_title='Axis vs Allies: Monthly NAV IC',
        fac_ylabel='Monthly Total Naval Industrial Capacity (IC)',
        fac_stats='Average Monthly Naval IC:',
        cfac_title='Axis vs Allies : Cumulative Produced NAV IC',
        cfac_ylabel='Cumulative Total Naval Industrial Capacity (IC)',
        cfac_stats='Final Cumulative Naval IC:',
        minor='Minor Monthly NAV IC', minor_cum='Minor Powers : Cumulative Produced NAV IC',
        sub='Sub-Faction Analysis: Monthly NAV IC'),
    'civ': dict(
        prefix='civilian', short='Civ', long='Civilian',
        individual='Monthly Civ IC', ylabel='Civilian Industrial Capacity (IC)',
        cum_title='Cumulative Civ IC Over Time',
        cum_ylabel='Cumulative Civilian Industrial Capacity (IC)',
        fac_title='Axis vs Allies : Monthly Civ IC',
        fac_ylabel='Monthly Total Civilian Industrial Capacity (IC)',
        fac_stats='Average Monthly Civilian IC:',
        cfac_title='Axis vs Allied Powers: Cumulative Civ IC',
        cfac_ylabel='Cumulative Total Civilian Industrial Capacity (IC)',
        cfac_stats='Final Cumulative Civilian IC:',
        minor=None, minor_cum=None, sub=None),
}


def make_all_graphs(dates, series, cfg, out):
    colors = cfg['colors']
    factions = cfg['factions']
    nation_faction = {n: f for f, members in factions.items() for n in members}
    minors = cfg['minor_countries']
    p = lambda name: os.path.join(out, name)  # noqa: E731

    for key in ('mil', 'nav', 'civ'):
        t = IC_TEXT[key]
        pre = t['prefix']
        cd = to_country_data(dates, series[key])
        cum = cumulative(cd)
        plot_individual(cd, colors, t['individual'], t['ylabel'], p(f'{pre}_ic_graph_individual.png'))
        plot_cumulative(cum, colors, t['cum_title'], t['cum_ylabel'], p(f'{pre}_ic_graph_cumulative.png'))
        fac = faction_sums(cd, factions)
        plot_axis_vs_allies(fac, t['fac_title'], t['fac_ylabel'], t['fac_stats'],
                            p(f'{pre}_ic_graph_monthly_factions.png'))
        cfac = {k: v for k, v in cumulative(fac).items()}
        plot_axis_vs_allies(cfac, t['cfac_title'], t['cfac_ylabel'], t['cfac_stats'],
                            p(f'{pre}_ic_graph_cumulative_factions.png'), label_kind='Total',
                            use_final=True)
        if t['minor'] and minors:
            mcd = {c: cd[c] for c in minors if c in cd}
            plot_individual(mcd, colors, t['minor'], t['ylabel'],
                            p(f'{pre}_ic_graph_minor_countries.png'), legend_fs=10, markersize=4)
            mcum = {c: cum[c] for c in minors if c in cum}
            plot_cumulative(mcum, colors, t['minor_cum'], t['cum_ylabel'],
                            p(f'{pre}_ic_graph_minor_countries_cumulative.png'), legend_fs=10)
        if t['sub'] and cfg['sub_factions']:
            plot_sub_factions(cd, cfg['sub_factions'], t['sub'], t['ylabel'], t['fac_stats'],
                              p(f'{pre}_ic_graph_detailed_factions.png'))

    # army manpower (the original only kept non-zero points)
    mcd = to_country_data(dates, series['manpower'], skip_zero=True)
    mcd = {c: d for c, d in mcd.items() if d['values']}
    plot_individual(mcd, colors, 'Total Manpower in the Army', 'Army Manpower in the Field',
                    p('army_manpower_graph_individual.png'), comma=True)
    plot_axis_vs_allies(faction_sums(mcd, factions), 'Axis vs Allies : Total Manpower in the Army',
                        'Monthly Total Army Manpower in the Field', 'Average Monthly Manpower:',
                        p('army_manpower_graph_monthly_factions.png'), comma=True)

    # division + air equipment cost timelines (faction members only, like the original)
    tracked = [n for n in cfg['nations'] if n in nation_faction]
    div = {n: series['division_cost'][n] for n in tracked}
    plot_nation_timeline(dates, div, nation_faction, colors,
                         'HOI4 Military Division Costs Over Time by Nation', 'Total Division Cost',
                         p('hoi4_nations_timeline.png'))
    plot_faction_timeline(dates, div, factions,
                          'HOI4 Military Division Costs: Axis vs Allies Over Time',
                          'Total Division Cost', p('hoi4_factions_timeline.png'))
    air = {n: series['air_cost'][n] for n in tracked}
    plot_nation_timeline(dates, air, nation_faction, colors, 'Deployed Air IC by Nation',
                         'Total Air Wing Equipment Cost', p('hoi4_air_wings_nations_timeline.png'))
    plot_faction_timeline(dates, air, factions, 'Axis vs Allies : Deployed Air IC',
                          'Total Air Wing Equipment Cost', p('hoi4_air_wings_factions_timeline.png'))


def write_csv(dates, series, cfg, path):
    cols = ('mil', 'civ', 'nav', 'manpower', 'division_cost', 'air_cost')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['date', 'nation', 'monthly_mil_ic', 'monthly_civ_ic', 'monthly_nav_ic',
                    'army_manpower', 'division_cost', 'air_wing_cost'])
        for i, d in enumerate(dates):
            for n in cfg['nations']:
                w.writerow([d.strftime('%Y.%m.%d'), n] + [round(series[c][n][i], 3) for c in cols])
    print(f"  wrote {os.path.basename(path)}")


# =====================================================================================
def pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title='Select the folder with your HOI4 saves')
        root.destroy()
        return folder or None
    except Exception:
        return None


def unpack_zip(path):
    dest = os.path.splitext(path)[0] + '_saves'
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        for m in z.infolist():
            if m.filename.lower().endswith('.hoi4'):
                target = os.path.join(dest, os.path.basename(m.filename))
                if not os.path.exists(target):
                    with z.open(m) as src, open(target, 'wb') as dst:
                        dst.write(src.read())
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description='Make HOI4 IC graphs from a folder of saves.')
    ap.add_argument('saves', nargs='?', help='folder with .hoi4 saves (or a .zip of them)')
    ap.add_argument('-o', '--out', help='output folder (default: <saves>/graphs)')
    ap.add_argument('-c', '--config', default=os.path.join(HERE, 'config.json'),
                    help='nations / factions / colours config (default: config.json)')
    ap.add_argument('-j', '--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 1),
                    help='parallel save parsers (default: CPU cores - 1)')
    ap.add_argument('--no-cache', action='store_true', help="don't reuse parsed-save cache")
    args = ap.parse_args(argv)

    folder = args.saves or pick_folder()
    if not folder:
        ap.error('no save folder given')
    if folder.lower().endswith('.zip'):
        folder = unpack_zip(folder)
    if not os.path.isdir(folder):
        ap.error(f'not a folder: {folder}')

    cfg = load_config(args.config)
    out = args.out or os.path.join(folder, 'graphs')
    os.makedirs(out, exist_ok=True)

    saves = find_saves(folder)
    if not saves:
        print(f'No .hoi4 files in {folder}')
        return 1
    print(f'Found {len(saves)} saves in {folder}')
    cache_dir = None if args.no_cache else os.path.join(out, '.cache')
    records, errors = scan_saves(saves, cache_dir, args.jobs)
    if not records:
        print('Nothing could be read.')
        return 1

    dates, series, info = build_series(records, cfg)
    print(f'{len(dates)} dates: {dates[0]:%Y.%m.%d} -> {dates[-1]:%Y.%m.%d}')
    print(f"Equipment cost library: {info['cost_library_size']} variants "
          f"({info['missing_division_costs']} division / {info['missing_air_costs']} air variants "
          f"used the default cost)")
    if all(v == 0 for s in series['mil'].values() for v in s):
        print('WARNING: all military IC is zero - the save format may have changed '
              '(token IDs in hoi4_save_parser.py).')

    print('Drawing graphs...')
    make_all_graphs(dates, series, cfg, out)
    write_csv(dates, series, cfg, os.path.join(out, 'hoi4_stats.csv'))
    if errors:
        print(f'{len(errors)} save(s) could not be read:')
        for pth, e in errors:
            print(f'  {os.path.basename(pth)}: {e}')
    print(f'Done. Graphs are in {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
