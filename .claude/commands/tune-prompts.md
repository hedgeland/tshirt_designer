# Tune Prompts

Read `src/prompt_templates.py` in full. This file contains all three prompt templates used in the t-shirt designer:

- `concepts_prompt` — asks Gemini to brainstorm design concepts from a theme
- `variants_prompt` — expands a chosen concept into stylistically distinct image prompts
- `style_suffix` — the shared style constraints appended to every image prompt

Also read `src/brainstorm.py` and `src/prompts.py` to understand how the templates are called and what variables they receive.

Then ask the user what they want to change:
- Which prompt is underperforming? (concepts too generic? variants too similar? images ignoring style instructions?)
- What output are they seeing that they don't want?
- What output are they not seeing that they want?

Based on their answer, suggest specific, targeted edits to `src/prompt_templates.py`. Explain the tradeoff for each change — why it might help and what it might break. Apply changes only after the user confirms.

After applying changes, remind the user to run the app and test the affected phase so they can evaluate the results before committing.
