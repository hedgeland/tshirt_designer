# Prompts — {{ theme }}

| | |
|---|---|
| Theme | {{ theme }} |
| Concept | {{ concept }} |
| Variants | {{ variant_count }} |
| Size | {{ size }} |
| Aspect Ratio | {{ aspect_ratio }} |
| Generated | {{ generated }} |

{% for prompt in prompts %}
## Variant {{ loop.index }}

```
{{ prompt }}
```
{% endfor %}
