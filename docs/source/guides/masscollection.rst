Start a mass collection of raw forensic artifacts
=================================================

**Why this matters.** Two things drive a mass collection. First, evidence
is volatile: artifacts are constantly overwritten and if you are not quickly
enough in capturing them you might not be able to find valuable context to be
added to the attacker timeline you're building. Second, you always want to be
able to go back to the original evidence files.

That's why we've built a built and pre-configured a hunt to collect raw forensic
evidence at scale. Cycas post-processes them and ingests the results into ADX.

**How to start it**

1. Log in to the Velociraptor GUI and open **Hunting**.
2. Locate the prepared hunts - the mass triage collection is
   ``HUNT007 - Mass triage collection to Cycas (direct upload)``.
3. Review the collection targets before starting (the hunt collects a broad set
   of artifacts from every matching endpoint).
4. Select the hunt and **start** it. Volatile live data is collected by the
   smaller hunts (``HUNT001``-``HUNT006``); start those too if you want that data.

As collections complete, they flow into Cycas and appear in the dashboard, then
in ADX ready for analysis with KQL.
