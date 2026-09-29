"""Re-parse every pinned Wikipedia revision into data/interim/ (reproducibility of the transformed datasets).

Fetches the pinned revisions through the MediaWiki API if they are not cached in data/raw/wikipedia/, then runs
each parser's main(). The 2026 tables use the revisions recorded in data/interim/polls_wiki_2026.revision.json.
Compare the result with the committed tables with `git diff --stat data/interim` (expected: no changes).
"""

from __future__ import annotations

import json

from brfc import config
from brfc.ingest import wiki_2014, wiki_2018, wiki_2022, wiki_2026


def main() -> None:
    wiki_2014.main()
    wiki_2018.main()
    wiki_2022.main()
    rev = json.loads((config.DATA / "interim" / "polls_wiki_2026.revision.json").read_text(encoding="utf-8"))
    wiki_2026.main(pt_oldid=int(rev["pt_oldid"]), en_oldid=int(rev["en_oldid"]))


if __name__ == "__main__":
    main()
