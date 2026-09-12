// @vitest-environment jsdom
// Stylesheet-contract tests, not geometry tests: real 390px/browser checks are E2E.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

// npm runs this suite from web/. Read source directly: Vitest intentionally stubs CSS imports.
const styles = readFileSync('src/styles.css', 'utf8');
const html = readFileSync('index.html', 'utf8');

let style: HTMLStyleElement;
let sheet: CSSStyleSheet;
beforeEach(() => {
  style = document.createElement('style');
  style.textContent = styles.replace(/^@import[^;]+;/m, '');
  document.head.append(style);
  sheet = style.sheet!;
});
afterEach(() => style.remove());

function media(condition: string) {
  return [...sheet.cssRules].find((rule) => rule instanceof CSSMediaRule && rule.conditionText === condition) as CSSMediaRule;
}
function rule(selector: string, rules: CSSRuleList = sheet.cssRules) {
  return [...rules].find((entry) => entry instanceof CSSStyleRule && entry.selectorText === selector) as CSSStyleRule;
}

describe('responsive and accessible stylesheet contracts', () => {
  it('uses a viewport that permits normal mobile scaling and zoom', () => {
    const document = new DOMParser().parseFromString(html, 'text/html');
    const viewport = document.querySelector('meta[name=viewport]')!.getAttribute('content');
    expect(viewport).toContain('width=device-width');
    expect(viewport).not.toMatch(/user-scalable=no|maximum-scale=1/);
  });
  it('stacks the pipeline below the main workspace at tablet/mobile widths', () => {
    const mobile = media('(max-width: 780px)');
    expect(rule('.studio-layout', mobile.cssRules).style.getPropertyValue('grid-template-columns')).toBe('minmax(0, 1fr)');
  });
  it('wraps the composer and gives hints their own row at 390px', () => {
    const mobile = media('(max-width: 480px)');
    expect(rule('.composer-footer', mobile.cssRules).style.getPropertyValue('flex-wrap')).toBe('wrap');
    expect(rule('.composer-footer > span:first-child', mobile.cssRules).style.getPropertyValue('flex-basis')).toBe('100%');
    expect(rule('.audio-stage:has(.end-session) .voice-controls', mobile.cssRules).style.getPropertyValue('grid-column')).toBe('1');
  });
  it('keeps desktop conversation height bounded and gives remaining space to the transcript', () => {
    const desktop = media('(min-width: 781px) and (min-height: 700px)');
    expect(rule('.workspace', desktop.cssRules).style.getPropertyValue('height')).toBe('calc(100dvh - 112px)');
    expect(rule('.conversation', desktop.cssRules).style.getPropertyValue('min-height')).toBe('0');
    expect(rule('.transcript', desktop.cssRules).style.getPropertyValue('flex')).toBe('1');
    expect(rule('.transcript-heading, .composer, .transcript-note', desktop.cssRules).style.getPropertyValue('flex-shrink')).toBe('0');
    expect(rule('.composer').style.getPropertyValue('position')).toBe('sticky');
  });
  it('removes nonessential mobile hero space rather than shrinking touch controls', () => {
    const mobile = media('(max-width: 480px)');
    expect(rule('.workspace-heading p', mobile.cssRules).style.getPropertyValue('display')).toBe('none');
    expect(rule('.transcript', mobile.cssRules).style.getPropertyValue('height')).toBe('136px');
    expect(rule('.app-header').style.getPropertyValue('min-height')).toBe('64px');
    expect(rule('.audio-meter').style.getPropertyValue('height')).toBe('36px');
  });
  it('keeps controls touch-sized and transcripts safely wrapping long text', () => {
    expect(rule('.button').style.getPropertyValue('min-height')).toBe('44px');
    expect(rule('.transcript').style.getPropertyValue('overflow-wrap')).toBe('anywhere');
    expect(rule('.workspace').style.getPropertyValue('min-width')).toBe('0');
  });
  it('provides visible keyboard focus and reduced-motion alternatives', () => {
    expect(rule(':focus-visible').style.getPropertyValue('outline')).toContain('3px');
    const reduced = media('(prefers-reduced-motion: reduce)');
    expect(rule('*, *::before, *::after', reduced.cssRules).style.getPropertyValue('transition')).toBe('none');
    expect(rule('*, *::before, *::after', reduced.cssRules).style.getPropertyValue('animation')).toBe('none');
  });
});
