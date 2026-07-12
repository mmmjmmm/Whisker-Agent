import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const uiRoot = new URL('../', import.meta.url)
const read = (path) => readFileSync(new URL(path, uiRoot), 'utf8')

const hexToLuminance = (hex) => {
  const channels = hex.match(/[0-9a-f]{2}/gi).map((value) => parseInt(value, 16) / 255)
  const linear = channels.map((value) =>
    value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  )
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

const contrastRatio = (first, second) => {
  const lighter = Math.max(hexToLuminance(first), hexToLuminance(second))
  const darker = Math.min(hexToLuminance(first), hexToLuminance(second))
  return (lighter + 0.05) / (darker + 0.05)
}

const readHexVariable = (css, name) => {
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6});`, 'i'))
  assert.ok(match, `missing --${name}`)
  return match[1]
}

test('defines the Runner palette and typography tokens', () => {
  const css = read('src/app/globals.css').toLowerCase()
  const required = [
    '--background: #fbfbf9;',
    '--foreground: #252116;',
    '--emphasis: #a95638;',
    '--runner-font-sans:',
    '--runner-font-display:',
    '--runner-font-mono:',
    '--color-emphasis: var(--emphasis);',
  ]

  for (const token of required) {
    assert.match(css, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
})

test('keeps normal text and accent colors at WCAG AA contrast', () => {
  const css = read('src/app/globals.css')
  const background = readHexVariable(css, 'background')

  for (const name of ['foreground', 'muted-foreground', 'emphasis']) {
    assert.ok(
      contrastRatio(readHexVariable(css, name), background) >= 4.5,
      `${name} must reach 4.5:1 against background`
    )
  }
})

test('defines shared Runner surfaces and reduced motion behavior', () => {
  const css = read('src/app/globals.css')
  for (const selector of [
    '.runner-grid',
    '.runner-command-surface',
    '.runner-floating-surface',
    '.runner-brand-mark',
    '@media (prefers-reduced-motion: reduce)',
  ]) {
    assert.match(css, new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
})

test('uses the semantic application background', () => {
  const layout = read('src/app/layout.tsx')
  assert.match(layout, /flex-1 bg-background h-screen overflow-hidden/)
  assert.doesNotMatch(layout, /bg-\[#f8f8f7\]/i)
})

test('shared controls use hard-edged Runner surfaces', () => {
  const button = read('src/components/ui/button.tsx')
  const input = read('src/components/ui/input.tsx')
  const textarea = read('src/components/ui/textarea.tsx')
  const dialog = read('src/components/ui/dialog.tsx')
  const switchSource = read('src/components/ui/switch.tsx')

  assert.match(button, /duration-200/)
  assert.doesNotMatch(button, /shadow-xs/)
  assert.doesNotMatch(input, /shadow-xs/)
  assert.doesNotMatch(textarea, /shadow-xs/)
  assert.match(dialog, /runner-floating-surface/)
  assert.match(switchSource, /data-\[state=checked\]:bg-emphasis/)
})

test('home and sidebar expose the Runner visual markers', () => {
  assert.match(read('src/app/page.tsx'), /runner-grid/)
  assert.match(read('src/app/page.tsx'), /font-display/)
  assert.match(read('src/components/chat-header.tsx'), /runner-brand-mark/)
  assert.match(read('src/components/left-panel.tsx'), /runner-grid/)
  assert.match(read('src/components/session-item.tsx'), /runner-active-row/)
  assert.match(read('src/components/chat-input.tsx'), /runner-command-surface/)
})

test('conversation surfaces use the Runner execution language', () => {
  const detail = read('src/components/session-detail-view.tsx')
  assert.doesNotMatch(detail, /#f8f8f7/i)
  assert.match(read('src/components/session-header.tsx'), /runner-panel-header/)
  assert.match(read('src/components/chat-message.tsx'), /runner-message-user/)
  assert.match(read('src/components/chat-message.tsx'), /runner-step-row/)
  assert.match(read('src/components/plan-panel.tsx'), /runner-panel/)
  assert.match(read('src/components/tool-use/tool-badge.tsx'), /font-meta/)
})
