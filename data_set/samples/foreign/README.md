# Real foreign / non-SDI invoices

Per the demo brief: drop your own real invoices here (Amazon, AWS, SaaS
subscriptions, hotel bills, customs — anything non-Italian or outside
SDI e-invoicing). Real, free, zero privacy issue, and it's exactly the
"non-SDI" document type TeamSystem's own pipeline doesn't ingest.

Nothing here is scripted or synthetic — this folder is intentionally
empty until you add your own files. Anything you drop in (PDF, image,
or text) flows through the pipeline the same way as any other sample:
via `POST /api/demo-1/process`, the Demo 1 page's upload button, or
`POST /api/demo-1/ingest-samples` if you add it under a recognized
`data_set/samples/<format>/` subfolder instead.
