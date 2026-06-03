About us
========

Cycas started as an internal tool at `Nerium Cyber Security <https://www.nerium.nl>`_ to allow our incident response investigators to focus on the investigation rather than the time-consuming work of collecting and processing forensic data. After using it across many engagements, we decided to make it publicly available so the broader DFIR community can benefit from it.

A core principle behind Cycas is that evidence should be collected at scale and preserved before any analysis takes place. Endpoint artefacts are volatile. The Windows Security event log, for example, often covers less than a day. Analysing endpoints directly risks missing evidence that has already been overwritten. Cycas collects at scale first, preserving the full triage package before any analysis touches it.

All processing happens in the cloud against the collected evidence, making the investigation auditable, repeatable, and independent of the state of the endpoint.

