# Tröskel

*Tröskel* ("threshold" in Swedish) is a desktop program for physiological testing and running gait analysis, made for coaches. It runs the test on the treadmill, the bike trainer or the SkiErg, reads the heart rate strap, films the athlete, and ends with a printed report: lactate thresholds, training zones, an estimated VO2max and the technique with marked pictures. The repository and the program file are called `lopanalys`.

The program, its interface and its documentation are in Swedish (an English interface exists for most texts). [Svenska: README.sv.md](README.sv.md)

## What it does

- **Runs the test protocol:** the program sets the speed, incline or power for each stage, counts down, tells the coach when to take a blood sample, and saves everything as it goes.
- **Live view on a TV:** the camera picture with the skeleton drawn on it, heart rate, speed or power, and the stage, in full screen for the athlete.
- **Analyses the result:** lactate thresholds (LT1 and LT2) with several methods side by side, zones in several systems (Seiler's three zones, Olympiatoppen I1–I5, Garmin, Polar, Friel), the values to enter in a Garmin watch, an estimated VO2max with its range, and a comparison with the previous test.
- **Measures technique from video:** overstride, shin angle, trunk lean, vertical oscillation, cadence and more for running; saddle height, knee and arm angles for a bike fit; trunk and knee angles for double poling. Pose estimation runs on the computer with MediaPipe.
- **Writes the report:** a PDF for printing or mail, a FIT file for Strava and Garmin Connect, and a training plan built on the test.

## Sports and tests

| Sport | Tests |
|---|---|
| Running (treadmill) | Lactate step test, max heart rate test, heart rate and pace zones without a lactate meter, running technique |
| Cycling (smart trainer) | Lactate step test, ramp test, FTP 20 minutes, bike fit |
| Concept2 SkiErg | Lactate step test, ramp test, double poling technique |

The lactate values are measured with a hand-held meter and typed in during the test.

## Equipment

| Equipment | How it connects | Status |
|---|---|---|
| Treadmill with Bluetooth FTMS | Bluetooth LE, the program sets speed and incline. Also through the QZ app, or by hand: the program shows the speed to set | Used in real tests, the checklist is not complete |
| Tacx NEO 2T and other FTMS trainers | Bluetooth LE, ERG mode holds the power | Not yet tried on real hardware |
| Concept2 PM5 (SkiErg) | Bluetooth LE, the monitor is read and the workout is set up on it | Not yet tried on real hardware |
| Heart rate strap | Bluetooth LE heart rate profile, and running cadence where the strap sends it | Used in real tests (Garmin HRM-Pro+), the checklist is not complete |
| Cameras | Webcams and capture cards, a GoPro HERO8 or later over USB, a phone or IP camera over the network, or a video file. Two cameras can film at once | GoPro over USB used in real tests, the rest not yet tried |

What has and has not been tried on real hardware is listed in [docs/PROV.md](docs/PROV.md) (Swedish). Everything is tested against simulators in the test suite, and a demo mode runs the whole program without any equipment.

## Build and run

Python 3.11 or later. The program is made for Windows; development and the tests also run on Linux.

```bash
python -m venv .venv
.venv/bin/pip install -e .[dev]
pytest
python -m lopanalys.ui.app --demo     # simulated treadmill, heart rate and a drawn runner
```

On Linux without a screen, source `scripts/headless_env.sh` before running the tests. The Windows build is made with PyInstaller (`pyinstaller --noconfirm packaging/lopanalys.spec`), and `.github/workflows/windows.yml` builds it on GitHub. The pose model is downloaded at build time and is not in the repository.

How the code is organised is described in [docs/ARKITEKTUR.md](docs/ARKITEKTUR.md) (Swedish).

## Data protection in short

Test results are health data, so the program is built to keep them local and to keep as little as possible:

- **Everything stays on the computer.** No cloud, no account, no telemetry. The only thing that leaves is the mail with the report, when the coach sends it to an athlete who has agreed to it.
- **Files are named by number, not by person,** and films are deleted 30 days after the test by default.
- **Consent is registered per athlete** and decides what the program does: no saved film without consent to film.
- **Erasure removes everything** the program can reach and lists what it cannot. An extract of a person's data can be exported.
- **The backup is encrypted** (AES-256-GCM), and the program can be locked with a PIN.
- **Not yet:** the database itself is not encrypted, so a lost computer is protected only by the operating system.

The details, and what the law requires of a coach, are in [docs/DATASKYDD.md](docs/DATASKYDD.md) (Swedish). The repository never contains real people: the tests, screenshots and examples use made-up athletes.

## Contributing

Contributions are welcome. Every commit is signed off under the Developer Certificate of Origin (`git commit -s`), see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Copyright 2026 GeneralTgul

Tröskel is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version (AGPL-3.0-or-later). The full text is in [LICENSE](LICENSE).

The program is distributed in the hope that it will be useful, but without any warranty; without even the implied warranty of merchantability or fitness for a particular purpose. It is not a medical device. If you run a modified version for others over a network, the AGPL requires you to offer them its source code.
