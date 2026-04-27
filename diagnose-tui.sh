#!/bin/bash
#odman-compose -f container/production/podman-compose.yml --profile tui build acc-tui
podman run --rm localhost/acc-tui:0.2.0 python3 -c "
import sys, pathlib, traceback
print('--- Import check ---', flush=True)
try:
    import acc.tui.app as m
    print('acc.tui.app: OK, file:', m.__file__, flush=True)
    css = pathlib.Path(m.__file__).parent / 'app.tcss'
    print('CSS:', css, '| exists:', css.exists(), flush=True)
    from acc.tui.app import ACCTUIApp
    print('ACCTUIApp: OK', flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(1)
print('--- All OK ---', flush=True)
" 2>&1

