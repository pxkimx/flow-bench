# Flow Bench

Flow cytometry analysis on your own Mac: open FCS files, compensate, gate, check, compare groups, and send the
numbers to GraphPad Prism, Excel or a PDF report. It runs as a small local server and opens in your browser;
nothing is uploaded.

**Download for Mac, screenshot and an example report:** [paul-h-kim.com/software/flow-bench](https://paul-h-kim.com/software/flow-bench)

It is organised the way ribbon-and-layout cytometry programs are: tools across the top, a data list and a gate
view on the left, a page of plots in the middle that all follow the current file, and the selected gate, the
Prism link and the checks on the right. Light and dark looks are built in.

## What it does

- **Files**: FCS 2.0, 3.0 and 3.1 (read with FlowIO). Drop files or a folder, or choose them. Files are read where
  they are and never changed. Roles (study sample, unstained, single-stain, FMO, isotype) and groups are guessed
  from the file names and shown for you to confirm.
- **Compensation**: the matrix stored in each file, one project matrix computed from single-stain controls or
  edited by hand, or none. Any pair of channels can be seen before and after.
- **Gates**: polygon, rectangle, range and quadrant, nested to any depth. Draw on one file and the gate applies to
  all; a file or a group can be given its own version, which is marked and reported.
- **Scales**: linear, log, arcsinh and logicle, as defined in Gating-ML 2.0. A gate keeps its place in the data
  when a scale changes.
- **Layout page**: density plots, histograms (with the FMO or unstained control drawn behind), a statistics table
  and a chart of one statistic by group. Previous / Next moves every object to another file.
- **Checks**: compensation left over in single-stain controls, gates that reach into an FMO control, gates with
  too few events (with a 95 % interval), uneven flow rate, events at a detector maximum, gates that differ between
  files, channels recorded at different voltages, missing controls. Every limit is yours to set.
- **Statistics**: one value per file (percent of parent, percent of total, events, median, geometric mean), and
  groups compared file against file, never event against event.
- **GraphPad Prism**: writes a `.pzfx` project with one Column table per statistic. Link a Prism file and
  "Update" replaces the numbers in the tables with the same titles and leaves everything else alone, so graphs
  and analyses made in Prism redraw from the new numbers. A dated backup is kept each time.
- **Tutorial**: a built-in page for people who have never analysed flow data: from raw files to gates, a graph
  and a table, with a diagram and the reason for each step, and the papers the advice comes from.
- **Also**: a PDF report (settings, checks, statistics, every plot of every study file), an Excel workbook,
  Gating-ML 2.0 export of the gates, and project files.

## Run it

Needs macOS and Python 3.10 to 3.14 (3.12 from python.org is the safe choice).

- **App**: download it from the page above, or build it: `macos/build_app.sh` builds `Flow Bench.app`. Double-click it. The first start creates a private Python
  environment in `~/Library/Application Support/Flow Bench/venv` (about a minute, needs internet once), then the
  app opens at `http://localhost:8800`. Closing its browser tab quits it completely.
- **From a terminal**: `./launch.sh`

## Example data

Two published datasets can be downloaded from inside the app (Home, Examples). They are not part of this
repository; they come from Zenodo and are checked against Zenodo's checksums. Both are CC BY 4.0.

| Example | Paper | Data |
|---|---|---|
| Cell-cycle reporter (FuChi), 16 files | Sudderick et al., *PLoS Biology* 2026, [doi:10.1371/journal.pbio.3004036](https://doi.org/10.1371/journal.pbio.3004036) | [Zenodo 21995904](https://zenodo.org/records/21995904) |
| Tumour immune panel with compensation controls, 25 files | Li et al., *Scientific Reports* 2025, [doi:10.1038/s41598-025-87687-0](https://doi.org/10.1038/s41598-025-87687-0) | [Zenodo 13977309](https://zenodo.org/records/13977309) |

The gates that open with the first example are a starting point drawn by Flow Bench. They are not the authors'
gates and the numbers they give are not results from the paper.

## How it was checked

- **Gating-ML 2.0 compliance set** (ISAC; fetched by `scripts/fetch_test_data.sh` from FlowKit's repository): all
  23 reference gates of the kinds Flow Bench has (polygons, rectangles and ranges; no compensation, the file's
  matrix or a given matrix; linear, log, arcsinh and logicle scales) select exactly the reference events.
  The set's other 18 definitions use things Flow Bench does not have (ellipsoid and Boolean gates, hyperlog and
  ratio scales) and are not tested.
- **Unit tests** on small made-up FCS files with a known spillover: compensation recovers the true signal, the
  matrix computed from controls matches the one that was put in, gate counts equal direct numpy counts, the four
  quadrants share out their parent exactly, a Prism file is updated in place without touching anything else.
- **Server**: answers only this Mac's own pages, and quits when its window closes.
- **GraphPad Prism 10.2.3**: a file written by Flow Bench opens without a warning; after a graph was made in
  Prism and the file saved, "Update" from Flow Bench changed the numbers and Prism redrew the graph from them.

`python -m pytest tests -q` runs all of it (20 tests).

## What it does not do yet

Ellipse and Boolean gates, FlowJo workspace import, proliferation and cell-cycle models, t-SNE / UMAP, spectral
unmixing, plate layouts. Logicle width is a setting, not estimated from the data. The check limits and the way a
matrix is computed from controls are proposals to be confirmed by whoever uses them.

## Built on

[FlowIO](https://github.com/whitews/FlowIO) and [FlowUtils](https://github.com/whitews/FlowUtils) (BSD) for
reading FCS files and for the logicle transform, numpy, SciPy, Matplotlib, ReportLab and openpyxl.

Flow Bench is an independent program. It is not affiliated with FlowJo, FCS Express or GraphPad.

## Licence

MIT. Copyright (c) 2026 Paul H. Kim.
