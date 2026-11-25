# Clipping Factory (local MVP)

Open **Clipping** in the existing Studio sidebar. This is an organic-only local workflow. It does not register accounts, scrape campaign websites, post videos, buy views or verify payouts automatically.

1. Open the discovery catalogs and manually copy a real campaign's URL, rules, permission, audience restrictions, CPM, per-post minimum, cap and deadline. Default amounts in the form are examples, not offers. Activate only after verifying the rules and rights. If a campaign already has clips, terms are locked: create a new campaign for revised terms.
2. Import permitted footage in the Library. Set usage to "мой материал" or "разрешено" (the latter requires provenance). Wait for probe and transcription. Reference-only footage is refused.
3. Select a campaign and source. Suggest up to ten non-overlapping transcript intervals or enter ranges manually. The shortlist is heuristic, not AI semantic analysis or a prediction of virality. Review context and hook notes before rendering.
4. Create a batch, then render one or up to ten clips sequentially. MP4: 1080×1920, 30 fps, H.264, with letterboxing (no smart crop), AAC when source has audio. Export SRT from ASR segments; optional burned captions require FFmpeg with libass. Review the result before posting. Files live under studio-data/exports/clipping/.
5. Post and submit manually, record a unique post URL and move the status from published to submitted, approved/rejected and paid. Enter only confirmed eligible views and actual receipts. Estimated cents = eligible views × CPM cents / 1000, capped per post and zero below threshold. It does not include campaign budget exhaustion, proration, taxes, commissions or time spent. Actual payout and cost are separate. Record each real receipt, not a forecast.

MCP tools: clipping_overview, clipping_suggest, clipping_create_batch, clipping_render. Use the existing creative methods to review hooks and context. Automatic campaign scanning, semantic AI ranking, graphical reframing, publication adapters, online dashboard sync, flexible payout models and platform analytics remain outside this MVP. No extra paid subscription or keys are required for the core flow. Local MLX transcription may need a model download.

Testing: python tests/clipping_smoke.py (portable dependencies as in CI). Full Mac acceptance and live campaign/payout checks remain necessary.
