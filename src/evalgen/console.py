"""Console encoding, fixed before this package is allowed to print anything.

A Windows console inherits the system's legacy codepage, which on a Thai-locale
machine is **cp874**. `sys.stdout` is opened with that encoding, and cp874 cannot
represent most of what this package produces: any print containing Thai raises
`UnicodeEncodeError` and takes the process down. Not a mangled character, not a
warning -- a traceback, on output, after the model call has already been paid for.

That makes it fatal in the most expensive possible place. `evalgen` generates Thai
call transcripts; a generator that dies when it tries to show you one is useless, and
the failure lands after the tokens are spent rather than before.

Hence `configure_stdout()` is called FIRST in `main()`, ahead of argument parsing,
config loading and any other work that might print. Anything that runs before it is
running on a stream that cannot say ยกเลิก.

`errors="replace"` rather than `errors="strict"` is deliberate at the second level of
defence: if a stream somewhere cannot be reconfigured, output should degrade to
question marks and stay readable rather than abort a generation run.
"""

from __future__ import annotations

import sys


def configure_stdout() -> None:
    """Reconfigure stdout and stderr to UTF-8. Call first in `main()`.

    Idempotent and safe to call when the streams are already UTF-8. The `hasattr`
    guard covers streams that are not `TextIOWrapper` -- a pytest capture object or a
    pipe wrapper may lack `reconfigure`, and refusing to start over a console detail
    would be a worse failure than the one this prevents.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
