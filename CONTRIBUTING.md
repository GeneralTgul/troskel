# Contributing to Tröskel

Thank you for helping. Tröskel is licensed under the [GNU Affero General Public License, version 3 or later](LICENSE), and every contribution is accepted under the same license.

## Sign off every commit (DCO)

Tröskel uses the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) instead of a contributor license agreement. By signing off a commit you certify the text below. Sign off with `git commit -s`, which adds a line with your name and e-mail address:

```
Signed-off-by: Your Name <you@example.com>
```

Use your real name or the name you are known by, and an address you want to be public. GitHub's `noreply` address works. A pull request with a commit that is not signed off cannot be merged. To sign off the commits you already made on a branch: `git rebase --signoff main`.

The certificate, as published at <https://developercertificate.org/> and in the Linux kernel's `Documentation/process/submitting-patches.rst`:

```
Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

## Before you open a pull request

- **Run the tests:** `pip install -e .[dev]` and `pytest`. On Linux without a screen, `source scripts/headless_env.sh` first.
- **Turn on the documentation checks** once per clone: `git config core.hooksPath tools/hooks`. The rules for the pages under `docs/` are in [docs/dokumentationsrutin.md](docs/dokumentationsrutin.md) (Swedish).
- **No real people or health data.** Tests, screenshots and examples use the made-up demo athletes, documentation addresses (`192.0.2.0/24`, `example.com`) and invented serial numbers. The test `tests/tools/test_no_private_data.py` stops the most common mistakes.
- **Code and comments are in English,** the documentation and the interface texts in Swedish, with English texts in `src/lopanalys/i18n/*.en.json`.
- **Say what was tried on real hardware.** Code that has only run against the simulators gets a row in [docs/PROV.md](docs/PROV.md).
