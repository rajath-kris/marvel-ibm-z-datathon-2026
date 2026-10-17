# Pipeline demo (50 s of the video)

Live (from the Mac; starts the vid-* demo stack, serves the page, removes the stack when you press Ctrl-C):

    ssh -i <your-key> -L 8300:127.0.0.1:8300 <user>@<vm-address> 'cd ~/fedsepsis-deploy && python3 demo/pipeline/serve.py --prep'

then open http://localhost:8300/ . Keys: 1 shadow mode, 2 re-tune, 3 federated round, 4 results, R reset
(R also rebuilds the demo stack, about 5 s, so the round can run again).

Replay of the saved real run (no VM, no docker; copy this folder to the Mac first):

    cd demo/pipeline && python3 serve.py --replay-only

then open http://localhost:8300/?replay=1 (keys play the saved steps) or ?replay=auto (plays all, 2.5 s apart).
The replay file records its date and a SHA-256 of the run; a changed file is refused.
`?replay=1&upto=N` draws the first N steps at once (still frames for screenshots).

Files: serve.py (server and step logic), index.html (page), round.sh (step 3: starts hospital B in the vid-* stack),
replay/replay-*.json (one real run), server.log. Nothing here touches hospA, hospB, rev-*, fedsepsis-aggregator or notebook.
