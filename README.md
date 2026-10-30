# HOI4 IC Graph Tool

Turns a folder of Hearts of Iron IV saves (e.g. the monthly autosaves of a multiplayer game)
into the full set of IC / manpower / division / air graphs, plus a CSV of the numbers.

It reads both HOI4 save formats, **binary** (the default for MP and autosaves) and
**plain text**, zipped or not, and you can mix them in one folder.

## Use it

1. Install Python 3.9+ and matplotlib (`pip install matplotlib`). `run_graphs.bat` installs matplotlib for you if it's missing.
2. Put all the saves of one game in a folder.
3. Drag that folder onto `run_graphs.bat`, or run:

```
python hoi4_graphs.py "C:\path\to\save folder"
python hoi4_graphs.py "C:\path\to\saves.zip" -o "C:\graphs\my game"
```

Graphs land in `<save folder>\graphs\`. Saves are parsed in parallel and cached in
`graphs\.cache`, so re-running after changing the config is instant.

Saves are ordered by the **in-game date** stored inside them (not file names or file times).
If several saves share the same in-game day, only the latest is used.

Every save is one data point, and the cumulative graphs add up each point's monthly value.
So for a game, put in the monthly autosaves only: an extra manual save mid-month
would count as an extra month in the cumulative totals.

## Per-game setup: `config.json`

- `nations`: which countries to track
- `factions`: members of `Axis` and `Allies`
- `sub_factions`: groups for the "Sub-Faction Analysis" graphs (name, colour, marker, members)
- `minor_countries`: countries shown in the "Minor ..." graphs
- `colors`: line colour per country

Use `-c other_game.json` to keep one config per game.

## What the numbers are

| Graph | Source in the save |
|---|---|
| MIL / NAV / Civ IC | sum of `speed` (daily IC output) over `military_lines` / `naval_lines` + `ship_refit_lines` / `general_lines`, x 30 |
| Army manpower | `army_manpower_value` of every division (including divisions in transit, e.g. volunteers), adjusted for the deployment queue exactly like the original script (`manpower_mode`) |
| Division cost | equipment amount in each division x unit IC cost (division credited to its expeditionary owner if lent) |
| Deployed air IC | equipment in each air wing x unit IC cost |

Unit costs come from the `cost` of military production lines, first value seen across the game.
Equipment that was never on a production line uses the defaults in the config (1.1 for division
equipment, 23 for planes), the same as the original scripts.

`hoi4_stats.csv` has every value per nation per date if you want your own charts.

## Notes

- Field names in binary saves are numeric tokens (see the top of `hoi4_save_parser.py`, which
  also maps each one to its plain-text name). They were mapped against HOI4 1.19 saves. If a
  future patch moves them, binary saves will warn that all military IC is zero; text saves
  aren't affected.
- Checked against the original scripts: on a 79-save plain-text game every IC and manpower
  value per nation per month is identical (4,740 values), and on a 91-save binary game all
  116 legend values in the original graphs match.

## License

MIT, see [LICENSE](LICENSE).
