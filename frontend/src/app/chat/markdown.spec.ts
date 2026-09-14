import { renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
  it('renders bold, italic, code and paragraphs', () => {
    const html = renderMarkdown('Según **Fogafín**, cubre *hasta* `50` millones.\n\nOtro párrafo.');
    expect(html).toBe('<p>Según <strong>Fogafín</strong>, cubre <em>hasta</em> <code>50</code> millones.</p><p>Otro párrafo.</p>');
  });

  it('renders bullet and numbered lists', () => {
    const html = renderMarkdown('Puntos:\n- uno\n- **dos**\n\n1. primero\n2. segundo');
    expect(html).toBe('<p>Puntos:</p><ul><li>uno</li><li><strong>dos</strong></li></ul><ol><li>primero</li><li>segundo</li></ol>');
  });

  it('escapes HTML coming from the model before adding any tag', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)> **ok**');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(html).not.toContain('<img');
    expect(html).toContain('<strong>ok</strong>');
  });

  it('does not treat a lone asterisk in a number as italics', () => {
    expect(renderMarkdown('1.5 * el interés')).toBe('<p>1.5 * el interés</p>');
  });
});
