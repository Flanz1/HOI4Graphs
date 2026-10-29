# HOI4 IC Graph Tool

Turns a folder of Hearts of Iron IV saves (e.g. the monthly autosaves of a multiplayer game)
into the full set of IC / manpower / division / air graphs, plus a CSV of the numbers.

It reads HOI4's **binary** saves directly (the default for MP and autosaves, zipped or not),
so you don't need to switch the game to plain-text saving first.

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

Saves are ordered by the **in-game date** stored inside them (not file names or file times),
and duplicate dates are ignored.

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
| MIL / NAV / Civ IC | sum of `speed` (daily IC output) over military / naval / construction lines, x 30 |
| Army manpower | `army_manpower_value` of every division, adjusted for the deployment queue the same way the original scripts did (`manpower_mode`) |
| Division cost | equipment amount in each division x unit IC cost (division credited to its expeditionary owner if lent) |
| Deployed air IC | equipment in each air wing x unit IC cost |

Unit costs come from the `cost` of military production lines, first value seen across the game.
Equipment that was never on a production line uses the defaults in the config (1.1 for division
equipment, 23 for planes), the same as the original scripts.

`hoi4_stats.csv` has every value per nation per date if you want your own charts.

## Notes

- Field names in binary saves are numeric tokens (see the top of `hoi4_save_parser.py`). They were
  mapped against HOI4 1.19.3 saves. If a future patch moves them, the tool warns that all
  military IC is zero.
- Plain-text (`HOI4txt`) saves aren't supported by this reader.

## License

MIT, see [LICENSE](LICENSE).
