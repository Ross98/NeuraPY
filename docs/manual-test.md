# NeuraPY Debug UI — Manual Test Checklist

## Standalone UI
- [ ] `python web/run.py --protocol neurapy --auto-start-camera --port 8765`
- [ ] Browser: http://127.0.0.1:8765 shows 3 panels
- [ ] Frame type dropdown lists: query, motion, status
- [ ] Build + Send round-trips: hex in textarea, log shows frame_out + frame_in
- [ ] Stop fake_camera via /api/disconnect, log shows disconnect

## Full closed loop
- [ ] `python web/run_debug.py --protocol neurapy` (Linux + neurapy)
- [ ] log shows connect (point_client -> fake_camera)
- [ ] Build + Send -> state bar updates
- [ ] Stop point_client (Ctrl-C), run_debug logs "exited N; restarting"

## Cross-platform
- [ ] `python -m unittest discover -s web/tests` green on macOS / Windows / Linux
- [ ] `python -m unittest test_binary.py` still green
- [ ] `bash scripts/check_platform.sh` reports OK

## New protocol flow
- [ ] `cp web/protocols/_template.py /tmp/my.py`
- [ ] Edit FRAME_SIZE + 4 methods
- [ ] `python web/run.py --protocol /tmp/my.py:MyProtocol` starts without error
