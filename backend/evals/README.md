# Live evaluations

The unit tests use a scripted fake model. These use the **real model** on realistic, messy work and score the result against answers known in advance.

    cd backend
    python -m evals.run                  # everything (about 5 minutes, about $2 of model use)
    python -m evals.run --only engine    # the sheet engine on 2,300 messy rows: no model, free
    python -m evals.run --only tasks     # Ethan, the real model, on sheet tasks
    python -m evals.run --only mission   # a whole mission: pasted brief, charter, hiring, pilot, 40 simulated calls, reports

It works in a throwaway data folder (never your real data) and reads only the model key and model names from `backend/.env`. It writes a scorecard and the full
conversations to `evals/results/` (ignored by git).

What is simulated: the phone line and the voice. Customers are AI test customers. The **caller is production-like**: the exact prompt the ElevenLabs agent gets (plus the
lively cues), the same tools (`lookup_info`, `send_information`, `ask_admin`, `end_call`, `do_not_call`…), the same model and the same 120-token reply ceiling.
What is not tested: ElevenLabs, Twilio, real audio, and how real people on a real phone behave.

`data.py` builds the sheets with ground truth: duplicates hidden behind different phone formats and capitals, a switchboard number shared by 15 people, 30 pairs of different
people with the same name, junk numbers, blank rows, stray spaces, and a second sheet with different headers and 270 people already on the first list.
Every duplicate is recognisable (they share a phone number or an email with the original), because a test nobody could pass would only measure the test.
