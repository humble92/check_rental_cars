# Meta model evaluation

Run date: November 16, 2026 (America/Toronto).

## Method

Two local images in `resources/test` were orientation-corrected, resized to fit 2048 x 2048, encoded as JPEG (quality 90), and submitted separately to each model. Endpoint: `https://api.meta.ai/v1/chat/completions`. The test used the application's inspection prompt, `reasoning_effort=low`, and `max_completion_tokens=3500`. It validated the returned JSON with the application's Pydantic schema. Credentials were read from `.env` and not logged. No email was sent.

| Model | Image | HTTP | Time | Input tokens | Output tokens (including reasoning) | Schema |
| --- | --- | --- | --- | --- | --- | --- |
| muse-spark-1.3-contributor | PXL_20260715_111115335.jpg | 200 | 12.97 s | 4,301 | 736 | Valid |
| muse-spark-1.3-contributor | PXL_20260715_125327012.jpg | 200 | 8.03 s | 4,301 | 624 | Valid |
| muse-ember-1.0 | PXL_20260715_111115335.jpg | 200 | 2.94 s | 4,301 | 516 | Valid |
| muse-ember-1.0 | PXL_20260715_125327012.jpg | 200 | 3.20 s | 4,301 | 378 | Valid |

## Observations

- Both models recognized the glossy exterior body-panel close-up and identified a visible crease/deformation near the seam.
- Both recognized the textured interior trim close-up and localized the damaged patch.
- Spark Contributor described lifted/gouged interior material; Ember called it a dent/depressed area. This illustrates why the report retains the visual description and uncertainty rather than treating the category as an expert diagnosis.
- Ember included an additional uncertain exterior scuff, and rated the reflection-heavy exterior image as limited quality. Spark called the same image good quality while listing reflections and tight cropping as limitations.
- Both returned valid normalized bounding boxes and English output, with no schema repairs needed.
- Ember was faster in this small run. This is a four-request smoke test, not a stable latency benchmark or a comparative damage-detection accuracy study.

## Selection and data use

The application uses the user's configured `MUSE_MODEL=muse-spark-1.3-contributor`. Both tested models are technically usable; change `MUSE_MODEL` and restart the web and worker processes to switch.

Contributor mode permits Meta to use submitted content and responses for model training. Ember is Standard-only. The UI discloses Contributor data use before analysis. The local seven-day retention policy cannot retract emailed/downloaded copies, provider processing records, or training uses. Verify provider policies before a public launch with customer photographs.

Official references:

- [Model overview](https://dev.meta.ai/docs/models)
- [Image understanding](https://dev.meta.ai/docs/media-understanding)
- [Chat completion parameters](https://dev.meta.ai/docs/protocols/chat-completions)
- [Pricing modes](https://dev.meta.ai/docs/pricing-rate-limits)

## Earlier Baseten attempt

The first image request to `https://inference.baseten.co/v1/chat/completions` returned HTTP 400 because model version `wpgy40e` was deactivated. Baseten image compatibility was not established. After the user requested Meta, the runtime integration was replaced; it does not silently fall back to Baseten.

## Application integration verification

The real configured Contributor model was subsequently exercised through the FastAPI upload endpoints and the production worker adapter (two further image calls). The report reached `ready`, and HTML, a three-page PDF, and the original-photo ZIP were downloaded successfully. The PDF pages were rendered and visually inspected, including evidence rectangles and readable numeric labels. The offline regression suite finished with 14 passing tests. No real SMTP delivery or browser/mobile visual check was completed.
