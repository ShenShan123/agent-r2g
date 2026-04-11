# ORFS Playbook

Use ORFS only after RTL syntax checks, testbench simulation, and Yosys synthesis have all passed successfully.

## Environment Setup

```bash
source /opt/openroad_tools_env.sh
```

This sets up:
- `OPENROAD_EXE=/usr/bin/openroad`
- `YOSYS_EXE=/opt/pdk_klayout_openroad/oss-cad-suite/bin/yosys`
- `STA_EXE=/usr/bin/sta`
- `KLAYOUT_CMD=/usr/bin/klayout`
- OSS CAD Suite in PATH

## ORFS Root

```
/opt/EDA4AI/OpenROAD-flow-scripts/
├── flow/
│   ├── Makefile          # Main flow driver
│   ├── platforms/        # PDK configurations
│   │   ├── nangate45/    # Default, fastest for testing
│   │   ├── sky130hd/
│   │   ├── sky130hs/
│   │   ├── asap7/
│   │   ├── gf180/
│   │   └── ihp-sg13g2/
│   ├── designs/          # Design configurations
│   └── scripts/          # ORFS internal TCL scripts
```

## Inputs to Prepare

1. **config.mk** - Design configuration
2. **constraint.sdc** - Timing constraints
3. **RTL file(s)** - Verilog source (absolute paths)

## config.mk Template

```makefile
export DESIGN_NAME = my_design
export PLATFORM    = nangate45

export VERILOG_FILES = /absolute/path/to/design.v
export SDC_FILE      = /absolute/path/to/constraint.sdc

export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
```

## constraint.sdc Template

```tcl
current_design my_design

set clk_name  core_clock
set clk_port_name clk
set clk_period 10.0
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
```

## Running ORFS

### Via Script (Recommended)
```bash
scripts/flow/run_orfs.sh <project-dir> [platform]
```

### Manually
```bash
cd /opt/EDA4AI/OpenROAD-flow-scripts/flow
make DESIGN_CONFIG=/path/to/config.mk
```

**Important:** Always pass `DESIGN_CONFIG` as a make argument, not an environment variable. The Makefile has a hardcoded default (gcd) that would override an env var.

## ORFS Output Directories

After a successful run:
- `results/<platform>/<design>/base/` - Final outputs (GDS, DEF, ODB, SPEF)
- `logs/<platform>/<design>/base/` - Stage logs
- `reports/<platform>/<design>/base/` - Timing, area, power, DRC reports
- `objects/<platform>/<design>/base/` - Intermediate objects

## Running Signoff Checks

After a successful ORFS backend run, run signoff checks using the ORFS results in-place:

### DRC (Design Rule Check)
```bash
scripts/flow/run_drc.sh <project-dir> [platform]
# or manually:
cd /opt/EDA4AI/OpenROAD-flow-scripts/flow
make DESIGN_CONFIG=/path/to/config.mk drc
```
- Invokes KLayout with platform-specific `.lydrc` rule file
- Outputs: `reports/<platform>/<design>/base/6_drc.lyrdb`, `6_drc_count.rpt`
- Script copies results to `<project>/drc/`

### LVS (Layout vs Schematic)
```bash
scripts/flow/run_lvs.sh <project-dir> [platform]
# or manually:
cd /opt/EDA4AI/OpenROAD-flow-scripts/flow
make DESIGN_CONFIG=/path/to/config.mk lvs
```
- Invokes KLayout with platform-specific `.lylvs` rule file and CDL netlist
- Not all platforms have LVS rules — script gracefully skips when unavailable
- Outputs: `6_lvs.lvsdb`, `6_lvs.log`
- Script copies results to `<project>/lvs/`

### RCX (Parasitic Extraction)
```bash
scripts/flow/run_rcx.sh <project-dir> [platform]
```
- Does NOT use ORFS Makefile; runs OpenROAD directly
- Generates `rcx/run_rcx.tcl` with commands:
  ```tcl
  read_db <6_final.odb>
  define_process_corner -ext_model_index 0 X
  extract_parasitics -ext_model_file <rcx_patterns.rules>
  write_spef <output.spef>
  ```
- Runs via `openroad -no_splash -exit rcx/run_rcx.tcl`
- Outputs: `rcx/6_final.spef`, `rcx/rcx.log`

## Default Assumptions for MVP

- Single top module
- No macros
- No custom floorplan beyond template defaults
- Focus on obtaining a runnable backend result with clean DRC and parasitic data

## Config Tuning Guidelines

### CORE_UTILIZATION Ranges
| Design Type | Recommended Utilization | Notes |
|-------------|------------------------|-------|
| Simple logic (UART, SPI, I2C) | 20-40% | Low routing demand |
| Medium (AES, SHA, FIR filters) | 15-30% | Moderate routing |
| Bus-heavy (crossbar, interconnect) | 10-15% | High routing demand |
| Macro-heavy (SRAM, CPU cores) | 30-40% | Macros occupy fixed area |

### PLACE_DENSITY_LB_ADDON Ranges
| Design Type | Recommended LB_ADDON | Notes |
|-------------|---------------------|-------|
| Small/simple designs | 0.10-0.20 | Low risk of divergence |
| Medium designs | 0.15-0.30 | Balanced |
| Large/macro-heavy designs | 0.20-0.45 | Prevents NesterovSolve divergence |
| **Minimum safe value** | **0.10** | Values below 0.10 risk placement stall |

**Never set PLACE_DENSITY_LB_ADDON below 0.05** — this reliably causes placement divergence on any non-trivial design.

### Safety Flags for Large Designs
For designs with > 50K instances or macros (swerv, black_parrot, ibex):
```makefile
export SKIP_CTS_REPAIR_TIMING = 1   # Prevents SIGSEGV in CTS timing repair
export SKIP_LAST_GASP = 1           # Prevents stalls in post-route optimization
```

## When Backend Fails

Check issues in the following order:

1. Wrong top module name or missing Verilog file
2. Malformed config.mk or constraint.sdc
3. Invalid clock port name or clock period
4. Design too large for default utilization target
5. Routing congestion (reduce utilization — see config tuning table above)
6. Placement divergence (increase PLACE_DENSITY_LB_ADDON to at least 0.15)
7. OpenROAD crash in CTS/repair_timing (add SKIP_CTS_REPAIR_TIMING = 1)
8. Environment or tool installation issues

Do not immediately rewrite RTL unless reports indicate an RTL-caused issue.

## When Signoff Checks Fail

1. **DRC violations:** Review `6_drc.lyrdb` for categories. Reduce density or increase area.
2. **LVS mismatch:** Review `6_lvs.lvsdb` for specifics. Check port names and connections.
3. **RCX failure:** Check `rcx.log` for OpenROAD errors. Verify ODB is valid and RCX rules exist.

## Platform Selection Guide

| Platform | Node | Speed | DRC | LVS | RCX | Use Case |
|----------|------|-------|-----|-----|-----|----------|
| nangate45 | 45nm | Fast | Yes | Yes | Yes | Quick testing, default, full signoff |
| sky130hd | 130nm | Medium | Yes | Yes | Yes | Open-source PDK, tapeout-ready, full signoff |
| sky130hs | 130nm | Medium | No | No | Yes | High-speed variant |
| asap7 | 7nm | Slow | Yes | No | Yes | Advanced node testing |
| gf180 | 180nm | Medium | No | No | Yes | GF open PDK |
| ihp-sg13g2 | 130nm | Medium | Yes | Yes | Yes | IHP SiGe BiCMOS, full signoff |
