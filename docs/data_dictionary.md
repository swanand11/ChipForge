# ChipForge Layer 1 Data Dictionary

Layer 1 emits raw EDA-like artifacts under `data/raw/runs/RUN_######/`. These reports are rendered from the internal `DesignTruth` model and intentionally not normalized; later ETL will parse these fields into canonical tables. Pipeline-level integrity results are written to `data/raw/data_integrity_report.json`.

## Bronze-to-Silver processing

Run `python scripts/run_pipeline.py --silver` to parse existing raw manifests and write explicit-schema Parquet datasets under `data/silver/`. Use `--silver-output s3a://bucket/prefix` for S3A. Spark is started with `org.apache.hadoop:hadoop-aws:3.3.4` and `com.amazonaws:aws-java-sdk-bundle:1.12.262`. These coordinates match the configured PySpark 3.5.8 / Hadoop 3.3.4 environment. Hadoop requires `hadoop-aws` and `hadoop-common` versions to match exactly; avoid adding other Hadoop or AWS SDK versions to Spark's classpath. Spark/Ivy needs Maven repository access, unless the matching JARs are preinstalled. The EC2 instance role provides credentials; no AWS keys are configured by this application. The run manifests and parsers are handled in the driver; Spark performs the schema-checked dataset writes. Spark log verbosity is controlled by `--spark-log-level` (default `WARN`). Do not run `--overwrite-silver` unless replacement is intended. Without it, an existing run_id in an output dataset is an error; previously unseen runs append.

The `chipforge.silver` modules separate report parsing, metric definitions, validation, schemas, and writing. The parser registry dispatches exclusively on manifest `artifact_type`. Parsers are synthetic ChipForge formats, versioned `1.0.0`; no vendor parser is implied. Each metric observation, timing path, cell statistic, or violation carries source artifact path, artifact type, parser name/version, and extraction timestamp. Row-level line ranges are included for timing paths, cell statistics, and violation table rows where available. Missing fields stay null, unsupported artifact types and parser errors are preserved in `run_artifacts` and lower parsing quality.

### Validation rules

All rules are run scoped; rules requiring absent reports return `NOT_APPLICABLE`. Tolerances can be overridden with repeated `--tolerance NAME=FLOAT` arguments (`timing_ns=0.001`, `area=0.02`, `power=0.0002`, `wire_length=0.02` defaults).

| Check | Formula / evidence | Category |
| --- | --- | --- |
| ARTIFACT_INVENTORY | Each listed manifest path exists and each observed file is listed; manifest.json itself is the inventory source. | artifact |
| PARSE_ERROR | A report must parse without parser errors. | schema |
| METRIC_VALIDATION | Registry minimum range checks, such as nonnegative counts, area, power, and wire length. | metric |
| TIMING_WNS | Reported WNS = min(setup path slack), tolerance in ns. | cross_report |
| TIMING_TNS | Reported TNS = sum(negative setup path slack), tolerance in ns. | cross_report |
| CELL_TOTAL | `total_cells` = sum of cell type rows. Category totals are not added to cell rows because they overlap. | cross_report |
| AREA_TOTAL | `cell_area` = combinational + sequential + buffer area, tolerance in um^2. | cross_report |
| POWER_TOTAL | `total_power` = sum of power category totals, tolerance in the synthetic report's common power unit. | cross_report |
| ROUTING_WIRE_LENGTH | `wire_length` = sum of metal layer wire lengths, tolerance in the synthetic report's common wire unit. | cross_report |
| STATUS_* | Compare identically named status fields between manifest and report only. No inference across differently defined status signals. | cross_report |

`validation_results` has check_id, run_id, name/category, expected/actual, difference, tolerance, PASS/FAIL/WARNING/NOT_APPLICABLE, and explanation. DesignTruth is used only in generator-backed tests as an independent oracle, never by production processing.

### Quality scoring

`run_quality` is separate from engineering statuses such as `overall_status`. Scores are percentages from 0 to 100. Completeness is the fraction of the five required types (`run_configuration`, `timing_summary`, `sta_setup`, `area_report`, `cell_statistics`) actually parsed; parsing success is successful artifacts divided by present manifest artifacts; schema validity checks required manifest fields; consistency is passing applicable validation checks divided by applicable checks. Overall score is `0.20C + 0.25P + 0.20S + 0.35R`, rounded to two decimals. HIGH is 95–100, MEDIUM 80–94.99, LOW below 80. Missing required artifacts or a failed critical reconciliation (timing WNS/TNS, cell total, area, power, or routing length) prevents HIGH. `failed_checks` and `warning_checks` count validation outcomes; an engineering constraint violation does not lower data quality by itself.

| field | source report | stage | kind | description | unit | example |
| --- | --- | --- | --- | --- | --- | --- |
| run_id | manifest.json, all reports | metadata | RAW | Unique run identifier. | id | RUN_000001 |
| design_id | manifest.json, all reports | metadata | RAW | Stable design identifier. | id | DESIGN_AES |
| flow_status | manifest.json, all reports | metadata | RAW | Whether the EDA flow executed and produced its expected artifacts. This is separate from engineering quality. | status | COMPLETED |
| timing_status | manifest.json | timing | DERIVED | PASS when setup and hold timing meet constraints, otherwise FAIL. | status | FAIL |
| routing_status | manifest.json | routing | DERIVED | PASS when routing overflow and unrouted nets are clean, otherwise FAIL. | status | PASS |
| drc_status | manifest.json | signoff | DERIVED | PASS when no DRC violations are present, otherwise FAIL. | status | FAIL |
| antenna_status | manifest.json | signoff | DERIVED | PASS when no antenna violations are present, otherwise FAIL. | status | PASS |
| lvs_status | manifest.json, signoff/lvs.rpt | signoff | RAW | LVS pass/fail status. | status | PASS |
| overall_status | manifest.json, all reports | metadata | DERIVED | CLEAN, CONSTRAINT_VIOLATION, or FLOW_FAILED based on flow and engineering statuses. | status | CONSTRAINT_VIOLATION |
| clock_period_ns | config/config.json | config | RAW | Target clock period used by the run. | ns | 1.250 |
| target_frequency_mhz | config/config.json | config | DERIVED | Frequency derived from clock period. | MHz | 800.0 |
| core_utilization_pct | config/config.json, floorplan/floorplan.rpt | config/floorplan | RAW | Requested or achieved core utilization. | percent | 70.0 |
| placement_density_pct | config/config.json | config | RAW | Target placement density. | percent | 75.0 |
| synthesis_effort | config/config.json | config | RAW | Mock synthesis optimization effort. | level | 3 |
| placement_effort | config/config.json | config | RAW | Mock placement optimization effort. | level | 3 |
| routing_effort | config/config.json | config | RAW | Mock routing optimization effort. | level | 3 |
| buffering_effort | config/config.json | config | RAW | Mock buffering optimization effort. | level | 3 |
| die_area_target | config/config.json | config | RAW | Target die area before generated floorplan dimensions. | square microns | 180000 |
| core_aspect_ratio | config/config.json | config | RAW | Width-to-height target for the core. | ratio | 1.10 |
| total_cells | synthesis/stat.rpt | synthesis | RAW | Total mapped standard-cell count. | count | 12450 |
| combinational_cells | synthesis/stat.rpt | synthesis | RAW | Non-sequential, non-buffer cell count. | count | 8200 |
| sequential_cells | synthesis/stat.rpt | synthesis | RAW | Register/latch cell count. | count | 3300 |
| buffer_cells | synthesis/stat.rpt | synthesis | RAW | Buffer cell count. | count | 950 |
| cell_area | synthesis/area.rpt | synthesis | RAW | Sum of mapped cell area. | square microns | 38250.44 |
| total_area | synthesis/area.rpt | synthesis | RAW | Cell area plus synthesis overhead margin. | square microns | 40100.92 |
| die_area | floorplan/floorplan.rpt | floorplan | RAW | Generated die area. | square microns | 68000.0 |
| core_area | floorplan/floorplan.rpt | floorplan | RAW | Generated core area. | square microns | 48500.0 |
| macro_count | floorplan/floorplan.rpt | floorplan | RAW | Number of generated macros. | count | 2 |
| standard_cell_count | floorplan/floorplan.rpt | floorplan | RAW | Standard cells passed into floorplanning. | count | 12450 |
| placed_cell_count | placement/placement.rpt | placement | RAW | Cells successfully placed. | count | 12450 |
| placement_utilization | placement/placement.rpt | placement | RAW | Achieved placement utilization. | percent | 76.2 |
| hpwl | placement/placement.rpt | placement | RAW | Half-perimeter wirelength estimate. | microns | 155300.7 |
| estimated_wirelength | placement/placement.rpt | placement | RAW | Placement-stage wirelength estimate. | microns | 184200.1 |
| routing_congestion_pct | placement/congestion.rpt | placement | RAW | Aggregate congestion estimate from placement. | percent | 71.4 |
| routing_overflow_pct | placement/congestion.rpt | placement | RAW | Estimated routing overflow after placement. | percent | 4.8 |
| clock_sink_count | cts/cts.rpt | cts | RAW | Number of sequential clock sinks. | count | 3370 |
| clock_buffer_count | cts/cts.rpt | cts | RAW | Number of inserted clock buffers. | count | 220 |
| clock_skew | cts/cts.rpt | cts | RAW | Max minus min clock skew. | ns | 0.083 |
| clock_wirelength | cts/cts.rpt | cts | RAW | Generated clock network wirelength. | microns | 14800.3 |
| slack | timing/setup.rpt, timing/hold.rpt | timing | RAW | Required time minus arrival time for a timing path. | ns | -0.087 |
| path_delay | timing/setup.rpt, timing/hold.rpt | timing | RAW | Data path delay for one timing path. | ns | 0.812 |
| logic_depth | timing/setup.rpt, timing/hold.rpt | timing | RAW | Number of logic stages in a path. | count | 17 |
| fanout | timing/setup.rpt, timing/hold.rpt | timing | RAW | Endpoint path fanout estimate. | count | 4 |
| wns | timing/timing_summary.rpt | timing | DERIVED | Worst setup slack from setup paths. | ns | -0.087 |
| tns | timing/timing_summary.rpt | timing | DERIVED | Sum of negative setup slacks. | ns | -0.441 |
| critical_path_delay | timing/timing_summary.rpt | timing | DERIVED | Maximum setup path delay. | ns | 1.087 |
| number_of_violating_endpoints | timing/timing_summary.rpt | timing | DERIVED | Count of setup paths with negative slack. | count | 5 |
| total_internal_power | power/power.rpt | power | RAW | Total internal cell power. | mW | 4.521 |
| total_switching_power | power/power.rpt | power | RAW | Total switching power. | mW | 5.118 |
| total_leakage_power | power/power.rpt | power | RAW | Total leakage power. | mW | 0.642 |
| total_power | power/power.rpt | power | DERIVED | Sum of internal, switching, and leakage power. | mW | 10.281 |
| wire_length | routing/routing.rpt | routing | RAW | Routed wire length summed across metal layers. | microns | 230140.5 |
| via_count | routing/routing.rpt | routing | RAW | Total generated via count. | count | 7100 |
| routed_net_count | routing/routing.rpt | routing | RAW | Nets completed by routing. | count | 9800 |
| unrouted_net_count | routing/routing.rpt | routing | RAW | Nets left unrouted. | count | 0 |
| metal_layer.utilization_pct | routing/routing.rpt | routing | RAW | Per-layer routing utilization. | percent | 62.5 |
| routing_overflow | routing/routing_congestion.rpt | routing | RAW | Final routing overflow. | percent | 2.1 |
| total_violations | signoff/drc.rpt | signoff | DERIVED | Count of DRC violation rows. | count | 18 |
| violation_type | signoff/drc.rpt | signoff | RAW | DRC rule category. | category | minimum_spacing |
| antenna_violations | signoff/antenna.rpt | signoff | DERIVED | Count of antenna violation rows. | count | 3 |
| timing_met | timing/timing_summary.rpt | timing | DERIVED | True when setup and hold WNS are non-negative. | boolean | false |
| signoff_clean | signoff/*.rpt | signoff | DERIVED | True when DRC, antenna, and LVS are clean. | boolean | true |
