# Kev 4B: System One Decision Model

> **[► Create an Instance](<<LAUNCH_LINK>>)**

## What is this template?

[Kev](https://github.com/jaredpalmer/kev) is an open family of **decision models**. You give it a document (the
*state*) and a set of typed questions, and it returns a probability for every allowed answer in a single forward
pass. It never writes text, so it cannot go off-schema, and it answers in tens to hundreds of milliseconds.

This template runs **Kev-4B**, the recommended starting point: most of Kev-9B's accuracy on a 12 GB card. It speaks the same `/v1/systemone` API as TypeSafe's hosted
Jev, so TypeSafe's SDKs work against it by changing the base URL.

## What can I do with this?

- **Route and triage**: which team, which queue, which intent.
- **Gate and check**: is this spam, does this need a human, does the evidence support the claim.
- **Score**: how urgent, how frustrated, how relevant, on a scale you define.
- Ask many questions about the same document in one request; each is answered independently.

Three question types:

| Type | You supply | You get |
|---|---|---|
| `choice` | named options, each with a description | the chosen option and a probability per option |
| `noul` | a yes/no question | the probability of yes |
| `score` | an ordered list of levels | the expected level and a probability per level |

## Quick start

1. **Rent** a GPU with at least 12 GB of VRAM through the [template link](<<LAUNCH_LINK>>).
2. Wait for provisioning: it installs Kev and downloads the model (the download is the longest step).
3. Open the **Instance Portal** and choose:
   - **Kev API**: interactive API docs.
   - **Kev Playground**: presets, your own documents and questions, an option-order test and a packed-vs-separate
     comparison. It opens once the model has loaded and warmed up.

### Calling the API

The API sits behind the instance's authentication. Its base URL is the portal's Kev API link **without** the
trailing `/docs` (the address and external port that map to 8000). Send the instance's **Open Button token** as
the bearer token:

```bash
curl https://<instance-address>:<kev-api-port>/v1/systemone \
  -H "Authorization: Bearer <open-button-token>" -H "Content-Type: application/json" \
  -d '{"model": "kev-latest",
       "state": "I was charged twice for March. Refund the duplicate or I cancel.",
       "questions": {
         "team":  {"type": "choice", "instructions": "Which team should handle this?",
                    "criteria": {"billing": "Charges and refunds", "shipping": "Delivery", "tech": "Bugs"}},
         "churn": {"type": "noul", "instructions": "Is the customer threatening to cancel?"}
       }}'
```

With TypeSafe's Python SDK (`pip install typesafe-sdk`), set `TYPESAFE_BASE_URL` to the same address and
`TYPESAFE_API_KEY` to the token.

## How good is it?

Measured on the same items as the recorded answers of TypeSafe's hosted Jev: Kev-9B is within 3-8 accuracy points
of Jev on held-out decision suites and ties it on support-ticket routing, and Kev-4B is within a few points of
Kev-9B. The largest gap is general-knowledge questions, which depend on the size of the base model. Always
measure on your own data before relying on a probability threshold.

## Good to know

- **One request at a time.** The server does not batch requests from different callers; this template is for
  evaluation and development rather than high-volume serving.
- **Context**: up to 8,192 tokens for the document plus one question. Longer requests are rejected.
- **Option order can matter.** Use the playground's option-order test to see how stable an answer is.
- **Memory**: the model loads in bf16 with its adapter unmerged (`KEV_MERGE=0`) so it fits a 12 GB card. On a
  larger card, set `KEV_MERGE=1` for about 30% faster responses.
- Need a different size? See the **Kev 9B** template.

## Licenses

- Kev code and weights: Apache-2.0 ([jaredpalmer/kev](https://github.com/jaredpalmer/kev))
- Base model: Qwen3.5 (Apache-2.0)
- Vast base image and templates: see [vast-ai/base-image](https://github.com/vast-ai/base-image)
