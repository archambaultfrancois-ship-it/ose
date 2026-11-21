# Subsystem development and integration example

This example includes an autonomous threshold detector, a real acquisition
neighbor, a recursive Monitoring definition and a local display component.

```text
Application
  acquisition.sensor → monitoring.detection.processor → display
```

`display` is an ordinary local component. Monitoring forwards its public ports
to the public ports of its Detection child. Detection exposes its internal
`processor` ports and a shared SDL `Settings` block as the public `threshold`
parameter. Acquisition publishes 90 every 100 simulated milliseconds; Detection
publishes values greater than or equal to its initial threshold of 80.

## Run the application

In Bash, enter this example's directory and prepare the OSE environment.
From the repository root:

```bash
cd ose/examples/subsystems
source ../../../setenv.sh
```

Run all commands below from this directory. Build and simulate the complete
application:

```sh
ose build
ose run --mode simulated --duration 0.35
```

`ose build` uses `model.json` and `deployment.json` in the current directory
and writes `build/plan.json`, which `ose run` uses by default. Use `--model`,
`--deployment` or `--build-dir` to select other paths. `ose validate` uses the
same source file defaults.

The display prints `ALARM 90.0` three times during the 350 ms simulation.

## Autonomous tests

```sh
ose subsystem validate detection
ose subsystem test detection --workers 2
```

The `nominal` case injects 20, 25 and 90 and expects exactly one output, 90, with
no output before 200 ms. `threshold` changes the public threshold to 24, injects
25 and expects it to pass. No Acquisition sources are needed for these commands
if selecting `--integration-profile standalone` explicitly during validation.

## Graphical authoring

From this example's directory with the environment prepared:

```sh
ose-editor --root .
```

The graph shows Acquisition, Monitoring and the local display with their public
connections. Choose **Open definition** on Monitoring, then on Detection, to
navigate the nested models. Breadcrumbs return to the parent. Edits change the
shared definition for all its instances. **Validate** checks the entire unsaved
application without executing handlers. **Instantiate subsystem**, **New
subsystem**, **Expose public port** and **Extract selection** prepare reviewable,
undoable source changes; **Save** writes them to disk.

## Integration with real Acquisition

```sh
ose subsystem test detection \
  --integration-profile with-acquisition --workers 2 \
  --report junit --output build/subsystems-junit.xml
```

The custom profile model instantiates Acquisition and `sut` and connects them.
It disables generic injection and runs the `integration` case, expecting three
90-valued messages in 350 ms. The collector remains on Detection's public output.
Scenarios differ by profile because real Acquisition supplies its own stimuli.

## Recursive definition and mixed application

```sh
ose subsystem test monitoring
```

To rebuild only the internal detector while retaining the full application plan:

```sh
ose build --component monitoring.detection.processor
```

See the [development guide](../../../docs/developpement-sous-systemes.md) and
[format reference](../../docs/subsystems.md). Artifacts under each definition's
`build/subsystem/` retain scenario envelopes, traces, collected JSON and verdicts.
