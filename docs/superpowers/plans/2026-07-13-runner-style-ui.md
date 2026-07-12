# Runner 风格 UI 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变功能、数据流和总体架构的前提下，将全部现有前端界面重构为 Runner 工作台化视觉风格。

**Architecture:** 先在 `globals.css` 建立集中式语义色彩、字体、圆角、点阵和表面样式，再让基础 UI 组件消费这些变量，最后按首页、会话、预览、设置和 Trace 的现有组件边界做局部样式迁移。使用一个零依赖 Node 样式契约测试验证关键设计标记，业务行为由现有代码保持不变，并通过 ESLint、生产构建和真实浏览器截图完成验收。

**Tech Stack:** Next.js 16、React 19、TypeScript、Tailwind CSS 4、Radix UI、Lucide React、Node.js 内置测试运行器、Chrome Headless

---

## Scope Check

该规格只涉及一个独立子系统：`ui/` 的视觉层。首页、会话、预览、设置和 Trace 共享同一套设计变量和基础组件，拆成多个独立计划会产生重复样式源，因此保留为一个计划并按现有组件边界分任务执行。

## File Map

**Create**

- `ui/tests/runner-style-contract.test.mjs`：零依赖静态样式契约，检查全局设计变量和关键页面的视觉标记。

**Modify: theme and primitives**

- `ui/src/app/globals.css`：Runner 色彩、字体、圆角、阴影、点阵纹理、减少动态效果和共享表面类。
- `ui/src/app/layout.tsx`：应用壳改用语义背景。
- `ui/src/components/ui/button.tsx`：硬边按钮和稳定交互状态。
- `ui/src/components/ui/input.tsx`：无软阴影的表单输入框。
- `ui/src/components/ui/textarea.tsx`：与 Input 一致的文本域。
- `ui/src/components/ui/dialog.tsx`：硬边浮层、遮罩和关闭按钮。
- `ui/src/components/ui/dropdown-menu.tsx`：硬边菜单浮层。
- `ui/src/components/ui/badge.tsx`：等宽状态标签。
- `ui/src/components/ui/kbd.tsx`：等宽键帽。
- `ui/src/components/ui/switch.tsx`：陶土橙选中状态。
- `ui/src/components/ui/sonner.tsx`：Toast 表面和阴影变量。

**Modify: shell and home**

- `ui/src/app/page.tsx`：编辑式首页空状态。
- `ui/src/components/chat-header.tsx`：现有品牌图标的 Runner 式标记。
- `ui/src/components/left-panel.tsx`：点阵侧栏头部和黑色主操作。
- `ui/src/components/session-item.tsx`：会话选中状态和信息层级。
- `ui/src/components/session-list.tsx`：骨架、空状态和错误状态的形状统一。
- `ui/src/components/suggested-questions.tsx`：紧凑描边建议条目。
- `ui/src/components/chat-input.tsx`：硬边命令输入框、模式分段控件和操作按钮。

**Modify: conversation**

- `ui/src/components/session-detail-view.tsx`：语义背景和右侧工具窗口间距。
- `ui/src/components/session-header.tsx`：平面固定标题栏。
- `ui/src/components/chat-message.tsx`：消息、步骤和工具执行时间线。
- `ui/src/components/attachments-message.tsx`：文件条目。
- `ui/src/components/plan-panel.tsx`：任务计划面板。
- `ui/src/components/markdown-content.tsx`：正文、引用、代码和链接颜色。
- `ui/src/components/tool-use/tool-badge.tsx`：紧凑等宽工具状态标签。

**Modify: previews, settings, and trace**

- `ui/src/components/file-preview-panel.tsx`：平面文件窗口。
- `ui/src/components/tool-preview-panel.tsx`：平面工具窗口和点阵标题栏。
- `ui/src/components/vnc-overlay.tsx`：远程桌面状态层和控制按钮外观。
- `ui/src/components/manus-settings.tsx`：设置窗口、导航和表单表面。
- `ui/src/components/skill-settings.tsx`：Skill 列表和源码预览表面。
- `ui/src/components/trace-panel.tsx`：控制台式 Trace 工作台。

不修改 `ui/src/hooks/`、`ui/src/lib/api/`、`ui/src/providers/`、路由结构、事件解析、VNC 连接逻辑或任何 `.env` 文件。

### Task 1: 建立全局 Runner 视觉系统

**Files:**

- Create: `ui/tests/runner-style-contract.test.mjs`
- Modify: `ui/src/app/globals.css`
- Modify: `ui/src/app/layout.tsx`

- [ ] **Step 1: 写入失败的全局样式契约测试**

创建 `ui/tests/runner-style-contract.test.mjs`：

```js
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
```

- [ ] **Step 2: 运行测试并确认失败原因正确**

Run:

```bash
cd ui && node --test tests/runner-style-contract.test.mjs
```

Expected: 4 个测试失败，失败信息分别指向缺失的 Runner 变量、颜色对比度、共享表面类和仍存在的 `bg-[#f8f8f7]`。

- [ ] **Step 3: 用集中式语义变量替换浅色主题**

保留现有滚动条规则和 `@custom-variant dark`，将 `@theme inline` 中的字体、颜色和圆角映射补全为：

```css
@theme inline {
    --font-sans: var(--runner-font-sans);
    --font-display: var(--runner-font-display);
    --font-mono: var(--runner-font-mono);
    --radius-sm: 3px;
    --radius-md: 4px;
    --radius-lg: 6px;
    --radius-xl: 8px;
    --radius-2xl: 10px;
    --radius-3xl: 12px;
    --radius-4xl: 14px;
    --color-background: var(--background);
    --color-foreground: var(--foreground);
    --color-card: var(--card);
    --color-card-foreground: var(--card-foreground);
    --color-popover: var(--popover);
    --color-popover-foreground: var(--popover-foreground);
    --color-primary: var(--primary);
    --color-primary-foreground: var(--primary-foreground);
    --color-secondary: var(--secondary);
    --color-secondary-foreground: var(--secondary-foreground);
    --color-muted: var(--muted);
    --color-muted-foreground: var(--muted-foreground);
    --color-accent: var(--accent);
    --color-accent-foreground: var(--accent-foreground);
    --color-emphasis: var(--emphasis);
    --color-emphasis-foreground: var(--emphasis-foreground);
    --color-destructive: var(--destructive);
    --color-border: var(--border);
    --color-input: var(--input);
    --color-ring: var(--ring);
    --color-chart-1: var(--chart-1);
    --color-chart-2: var(--chart-2);
    --color-chart-3: var(--chart-3);
    --color-chart-4: var(--chart-4);
    --color-chart-5: var(--chart-5);
    --color-sidebar: var(--sidebar);
    --color-sidebar-foreground: var(--sidebar-foreground);
    --color-sidebar-primary: var(--sidebar-primary);
    --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
    --color-sidebar-accent: var(--sidebar-accent);
    --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
    --color-sidebar-border: var(--sidebar-border);
    --color-sidebar-ring: var(--sidebar-ring);
    --color-gray-50: var(--neutral-50);
    --color-gray-100: var(--neutral-100);
    --color-gray-200: var(--neutral-200);
    --color-gray-300: var(--neutral-300);
    --color-gray-400: var(--neutral-400);
    --color-gray-500: var(--neutral-500);
    --color-gray-600: var(--neutral-600);
    --color-gray-700: var(--neutral-700);
    --color-gray-800: var(--neutral-800);
    --color-gray-900: var(--neutral-900);
    --color-gray-950: var(--neutral-950);
}
```

用以下浅色变量替换当前 `:root`，原有 chart 变量继续保留在该块末尾：

```css
:root {
    --runner-font-sans: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    --runner-font-display: "Iowan Old Style", "Palatino Linotype", "Songti SC", STSong, Georgia, serif;
    --runner-font-mono: "SFMono-Regular", Consolas, "Liberation Mono", "PingFang SC", monospace;
    --radius: 0.375rem;
    --background: #fbfbf9;
    --foreground: #252116;
    --card: #ffffff;
    --card-foreground: #252116;
    --popover: #ffffff;
    --popover-foreground: #252116;
    --primary: #252116;
    --primary-foreground: #fbfbf9;
    --secondary: #f0efea;
    --secondary-foreground: #252116;
    --muted: #f0efea;
    --muted-foreground: #67645e;
    --accent: #e9e7e1;
    --accent-foreground: #252116;
    --emphasis: #a95638;
    --emphasis-foreground: #fffaf6;
    --destructive: #b42318;
    --border: #d7d4cc;
    --input: #8f8b84;
    --ring: #a95638;
    --chart-1: oklch(0.646 0.222 41.116);
    --chart-2: oklch(0.6 0.118 184.704);
    --chart-3: oklch(0.398 0.07 227.392);
    --chart-4: oklch(0.828 0.189 84.429);
    --chart-5: oklch(0.769 0.188 70.08);
    --neutral-50: #f8f8f6;
    --neutral-100: #f0efeb;
    --neutral-200: #e2dfd8;
    --neutral-300: #cbc7be;
    --neutral-400: #a29e96;
    --neutral-500: #77736c;
    --neutral-600: #5e5a54;
    --neutral-700: #46423d;
    --neutral-800: #322e2a;
    --neutral-900: #252116;
    --neutral-950: #181611;
    --sidebar: #f2f1ed;
    --sidebar-foreground: #252116;
    --sidebar-primary: #252116;
    --sidebar-primary-foreground: #fbfbf9;
    --sidebar-accent: #e7e5de;
    --sidebar-accent-foreground: #252116;
    --sidebar-border: #cbc7be;
    --sidebar-ring: #a95638;
    --runner-dot: rgb(37 33 22 / 10%);
    --runner-hard-shadow: rgb(37 33 22 / 12%);
}
```

在 `.dark` 中增加可访问的 Runner 强调色，不删除其余现有暗色变量：

```css
.dark {
    --emphasis: #d68a63;
    --emphasis-foreground: #181611;
    --neutral-50: #1f1e1a;
    --neutral-100: #292721;
    --neutral-200: #3a3730;
    --neutral-300: #504c43;
    --neutral-400: #858078;
    --neutral-500: #aaa59d;
    --neutral-600: #c0bcb4;
    --neutral-700: #d6d2ca;
    --neutral-800: #e8e5df;
    --neutral-900: #f5f3ef;
    --neutral-950: #fbfbf9;
    --runner-dot: rgb(255 255 255 / 14%);
    --runner-hard-shadow: rgb(0 0 0 / 32%);
}
```

- [ ] **Step 4: 添加共享字体、点阵和表面样式**

在 `globals.css` 末尾加入：

```css
@layer components {
    .font-display {
        font-family: var(--runner-font-display);
    }

    .font-meta {
        font-family: var(--runner-font-mono);
        font-variant-numeric: tabular-nums;
        letter-spacing: 0;
    }

    .runner-grid {
        background-color: var(--background);
        background-image: radial-gradient(circle, var(--runner-dot) 0.75px, transparent 0.8px);
        background-size: 4px 4px;
    }

    .runner-command-surface {
        border: 1px solid var(--foreground);
        border-radius: 6px;
        background: var(--card);
        box-shadow: 4px 4px 0 var(--runner-hard-shadow);
    }

    .runner-floating-surface {
        border: 1px solid var(--foreground);
        border-radius: 6px;
        box-shadow: 6px 6px 0 var(--runner-hard-shadow);
    }

    .runner-brand-mark {
        border: 1px solid var(--border);
        border-radius: 4px;
        background-color: var(--card);
        background-image: url('/icon.png');
        background-position: center;
        background-repeat: no-repeat;
        background-size: 28px 28px;
    }
}

@layer base {
    html {
        background: var(--background);
    }

    body {
        background: var(--background);
        color: var(--foreground);
        font-family: var(--runner-font-sans);
    }

    ::selection {
        background: color-mix(in srgb, var(--emphasis) 28%, transparent);
        color: var(--foreground);
    }
}

@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        scroll-behavior: auto !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
}
```

- [ ] **Step 5: 让应用壳使用语义背景**

在 `ui/src/app/layout.tsx` 中将：

```tsx
<div className="flex-1 bg-[#f8f8f7] h-screen overflow-hidden">
```

替换为：

```tsx
<div className="flex-1 bg-background h-screen overflow-hidden">
```

- [ ] **Step 6: 运行契约测试与 ESLint**

Run:

```bash
cd ui && node --test tests/runner-style-contract.test.mjs && npm run lint
```

Expected: 4 个契约测试通过；ESLint 退出码为 0。

- [ ] **Step 7: 提交全局视觉系统**

```bash
git add ui/tests/runner-style-contract.test.mjs ui/src/app/globals.css ui/src/app/layout.tsx
git commit -m "style: establish Runner visual system"
```

### Task 2: 统一基础 UI 控件

**Files:**

- Modify: `ui/tests/runner-style-contract.test.mjs`
- Modify: `ui/src/components/ui/button.tsx`
- Modify: `ui/src/components/ui/input.tsx`
- Modify: `ui/src/components/ui/textarea.tsx`
- Modify: `ui/src/components/ui/dialog.tsx`
- Modify: `ui/src/components/ui/dropdown-menu.tsx`
- Modify: `ui/src/components/ui/badge.tsx`
- Modify: `ui/src/components/ui/kbd.tsx`
- Modify: `ui/src/components/ui/switch.tsx`
- Modify: `ui/src/components/ui/sonner.tsx`

- [ ] **Step 1: 增加基础控件契约并确认失败**

在契约测试末尾加入：

```js
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
```

Run:

```bash
cd ui && node --test tests/runner-style-contract.test.mjs
```

Expected: 新增测试失败，指出软阴影、缺少统一时长、Dialog 表面和 Switch 选中色尚未迁移。

- [ ] **Step 2: 修改 Button、Input 和 Textarea 的精确基础类**

将 `buttonVariants` 的基础类替换为：

```tsx
"inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-[color,background-color,border-color,opacity] duration-200 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:border-ring focus-visible:ring-ring/30 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive"
```

Button variant 使用以下精确值：

```tsx
default: "bg-primary text-primary-foreground hover:bg-primary/90",
outline: "border border-border bg-card hover:border-foreground/40 hover:bg-accent hover:text-accent-foreground",
secondary: "border border-border bg-secondary text-secondary-foreground hover:bg-accent",
ghost: "hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50",
link: "text-foreground underline decoration-emphasis underline-offset-4 hover:text-emphasis",
```

Input 基础类替换为：

```tsx
"file:text-foreground placeholder:text-muted-foreground selection:bg-emphasis selection:text-emphasis-foreground dark:bg-input/30 border-input h-9 w-full min-w-0 rounded-md border bg-card px-3 py-1 text-base transition-[color,border-color,box-shadow] duration-200 outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm"
```

Textarea 基础类替换为：

```tsx
"border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/30 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:bg-input/30 flex field-sizing-content min-h-16 w-full rounded-md border bg-card px-3 py-2 text-base transition-[color,border-color,box-shadow] duration-200 outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm"
```

- [ ] **Step 3: 修改浮层和状态控件**

在 `dialog.tsx` 的 `DialogContent` 基础类中删除 `rounded-lg` 和 `shadow-lg`，并加入 `runner-floating-surface`；Overlay 使用 `bg-black/55`。最终关键片段为：

```tsx
"bg-background runner-floating-surface data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 fixed top-[50%] left-[50%] z-50 grid w-full max-w-[calc(100%-2rem)] translate-x-[-50%] translate-y-[-50%] gap-4 p-6 duration-200 outline-none sm:max-w-lg"
```

在 `dropdown-menu.tsx` 中将两个内容容器的 `shadow-md` 和 `shadow-lg` 精确替换为 `runner-floating-surface`，保留所有 Radix 动画类。

将 Badge 基础类开头改为：

```tsx
"font-meta inline-flex items-center justify-center rounded-full border border-transparent px-2 py-0.5 text-xs font-medium"
```

将 Kbd 视觉类改为：

```tsx
"font-meta bg-card text-muted-foreground pointer-events-none inline-flex h-5 w-fit min-w-5 items-center justify-center gap-1 rounded-sm border border-border px-1 text-xs font-medium select-none"
```

Switch 根类删除 `shadow-xs`，并把：

```tsx
data-[state=checked]:bg-primary
```

替换为：

```tsx
data-[state=checked]:bg-emphasis
```

在 `sonner.tsx` 的 style 对象增加：

```tsx
"--normal-shadow": "4px 4px 0 var(--runner-hard-shadow)",
```

- [ ] **Step 4: 运行契约测试与 ESLint**

```bash
cd ui && node --test tests/runner-style-contract.test.mjs && npm run lint
```

Expected: 所有契约测试通过；ESLint 退出码为 0。

- [ ] **Step 5: 提交基础控件样式**

```bash
git add ui/tests/runner-style-contract.test.mjs ui/src/components/ui/button.tsx ui/src/components/ui/input.tsx ui/src/components/ui/textarea.tsx ui/src/components/ui/dialog.tsx ui/src/components/ui/dropdown-menu.tsx ui/src/components/ui/badge.tsx ui/src/components/ui/kbd.tsx ui/src/components/ui/switch.tsx ui/src/components/ui/sonner.tsx
git commit -m "style: unify Runner interface primitives"
```

### Task 3: 重构应用壳、首页与侧栏

**Files:**

- Modify: `ui/tests/runner-style-contract.test.mjs`
- Modify: `ui/src/app/page.tsx`
- Modify: `ui/src/components/chat-header.tsx`
- Modify: `ui/src/components/left-panel.tsx`
- Modify: `ui/src/components/session-item.tsx`
- Modify: `ui/src/components/session-list.tsx`
- Modify: `ui/src/components/suggested-questions.tsx`
- Modify: `ui/src/components/chat-input.tsx`

- [ ] **Step 1: 增加首页和侧栏契约并确认失败**

在契约测试末尾加入：

```js
test('home and sidebar expose the Runner visual markers', () => {
  assert.match(read('src/app/page.tsx'), /runner-grid/)
  assert.match(read('src/app/page.tsx'), /font-display/)
  assert.match(read('src/components/chat-header.tsx'), /runner-brand-mark/)
  assert.match(read('src/components/left-panel.tsx'), /runner-grid/)
  assert.match(read('src/components/session-item.tsx'), /runner-active-row/)
  assert.match(read('src/components/chat-input.tsx'), /runner-command-surface/)
})
```

Run:

```bash
cd ui && node --test tests/runner-style-contract.test.mjs
```

Expected: 新增测试失败，6 个页面级视觉标记均不存在。

- [ ] **Step 2: 添加会话选中样式**

在 `globals.css` 的 `@layer components` 中增加：

```css
.runner-active-row {
    position: relative;
    border-color: var(--border);
    background: var(--card);
}

.runner-active-row::before {
    position: absolute;
    inset-block: 8px;
    left: -1px;
    width: 3px;
    background: var(--emphasis);
    content: "";
}
```

- [ ] **Step 3: 重构首页和品牌标记的样式类**

`page.tsx` 使用以下关键类，不改变 JSX 层级或事件处理：

```tsx
<div className="h-full flex flex-col bg-background">
<div className="runner-grid flex-1 flex items-center justify-center px-4 py-6 sm:py-8 -mt-12 sm:-mt-16">
<div className="w-full max-w-full sm:max-w-[768px] sm:min-w-[390px] mx-auto">
<div className="font-display text-[28px] sm:text-[40px] leading-[1.08] font-normal mb-5 sm:mb-7 text-center sm:text-left">
<div className="text-foreground">您好, 慕学员</div>
<div className="text-emphasis">我能为您做什么?</div>
```

`chat-header.tsx` 的 Link 改为：

```tsx
<Link
  href="/"
  aria-label="返回首页"
  className="runner-brand-mark block size-10"
/>
```

- [ ] **Step 4: 重构侧栏和会话列表样式类**

`left-panel.tsx` 使用：

```tsx
<SidebarHeader className="runner-grid min-h-14 border-b border-sidebar-border">
<SidebarContent className="p-2.5">
<Button
  className="mb-3 w-full cursor-pointer justify-start border border-foreground"
  onClick={() => router.push('/')}
>
```

保持按钮 children 和点击逻辑原样，并给现有 `KbdGroup` 增加 `className="ml-auto"`。

`session-item.tsx` 的 Item className 改为：

```tsx
className={`relative items-start gap-2 border p-2.5 cursor-pointer hover:border-border hover:bg-card ${isActive ? 'runner-active-row' : 'border-transparent'}`}
```

标题使用 `text-foreground`，摘要和时间使用 `text-muted-foreground font-meta`。会话图标 Avatar 改为小圆角方形，不修改运行状态图标和动画。

`session-list.tsx` 的三个骨架块将 `rounded-full` / `rounded` 改为 `rounded-sm`，错误重试链接使用 `text-emphasis`。

- [ ] **Step 5: 重构推荐问题和 ChatInput**

推荐问题按钮使用：

```tsx
className="h-auto min-h-9 cursor-pointer whitespace-normal break-words border-foreground/20 bg-card px-3 py-2 text-left text-xs hover:border-emphasis hover:bg-secondary sm:text-sm"
```

ChatInput 根节点改为：

```tsx
<div className={cn('runner-command-surface flex w-full flex-col py-3', className)}>
```

输入框 className 改为：

```tsx
className="scrollbar-hide h-[46px] min-h-[40px] w-full resize-none bg-transparent text-sm leading-relaxed outline-none placeholder:text-muted-foreground"
```

Skill 建议浮层使用 `runner-floating-surface`；附件和发送图标按钮使用 `rounded-md`；模式组保留胶囊形态，但改为 `border-border bg-secondary`，当前模式仍由原 variant 逻辑决定。

- [ ] **Step 6: 运行契约测试与 ESLint**

```bash
cd ui && node --test tests/runner-style-contract.test.mjs && npm run lint
```

Expected: 所有契约测试通过；ESLint 退出码为 0。

- [ ] **Step 7: 提交应用壳与首页样式**

```bash
git add ui/tests/runner-style-contract.test.mjs ui/src/app/globals.css ui/src/app/page.tsx ui/src/components/chat-header.tsx ui/src/components/left-panel.tsx ui/src/components/session-item.tsx ui/src/components/session-list.tsx ui/src/components/suggested-questions.tsx ui/src/components/chat-input.tsx
git commit -m "style: restyle Runner home and session rail"
```

### Task 4: 重构对话与执行时间线

**Files:**

- Modify: `ui/tests/runner-style-contract.test.mjs`
- Modify: `ui/src/app/globals.css`
- Modify: `ui/src/components/session-detail-view.tsx`
- Modify: `ui/src/components/session-header.tsx`
- Modify: `ui/src/components/chat-message.tsx`
- Modify: `ui/src/components/attachments-message.tsx`
- Modify: `ui/src/components/plan-panel.tsx`
- Modify: `ui/src/components/markdown-content.tsx`
- Modify: `ui/src/components/tool-use/tool-badge.tsx`

- [ ] **Step 1: 增加对话视觉契约并确认失败**

```js
test('conversation surfaces use the Runner execution language', () => {
  const detail = read('src/components/session-detail-view.tsx')
  assert.doesNotMatch(detail, /#f8f8f7/i)
  assert.match(read('src/components/session-header.tsx'), /runner-panel-header/)
  assert.match(read('src/components/chat-message.tsx'), /runner-message-user/)
  assert.match(read('src/components/chat-message.tsx'), /runner-step-row/)
  assert.match(read('src/components/plan-panel.tsx'), /runner-panel/)
  assert.match(read('src/components/tool-use/tool-badge.tsx'), /font-meta/)
})
```

Run:

```bash
cd ui && node --test tests/runner-style-contract.test.mjs
```

Expected: 新增测试失败并指出旧背景和缺少的执行界面标记。

- [ ] **Step 2: 添加对话与执行记录共享类**

在 `globals.css` 的 `@layer components` 中加入：

```css
.runner-panel {
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--card);
}

.runner-panel-header {
    border-bottom: 1px dashed var(--border);
    background-color: var(--background);
    background-image: radial-gradient(circle, var(--runner-dot) 0.75px, transparent 0.8px);
    background-size: 4px 4px;
}

.runner-message-user {
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--card);
    box-shadow: 2px 2px 0 var(--runner-hard-shadow);
}

.runner-step-row {
    border-radius: 4px;
    color: var(--foreground);
}
```

- [ ] **Step 3: 重构会话壳和标题栏样式**

在 `session-detail-view.tsx` 中把底部输入容器：

```tsx
<div className="flex-shrink-0 bg-[#f8f8f7] py-4">
```

替换为：

```tsx
<div className="flex-shrink-0 border-t border-dashed border-border bg-background py-4">
```

加载、空状态和思考状态使用 `text-muted-foreground`；错误路径和所有行为保持原样。

`session-header.tsx` 根 header 改为：

```tsx
<header className="runner-panel-header flex flex-row items-center justify-between gap-2 sticky top-0 z-10 flex-shrink-0 px-1 pt-3 pb-2">
```

标题使用 `text-foreground text-base font-medium`，操作按钮位置不变。

- [ ] **Step 4: 重构消息、步骤和附件样式**

用户消息容器使用：

```tsx
<div className="runner-message-user relative flex items-center overflow-hidden p-3 text-foreground">
```

StepBlock 可交互行使用：

```tsx
className="runner-step-row text-sm w-full cursor-pointer flex gap-2 justify-between group/header truncate hover:bg-secondary transition-colors duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
```

完成标记使用 `border-emphasis bg-emphasis`，时间轴使用 `border-emphasis/40`。ToolRow 时间使用 `font-meta text-muted-foreground`，不改变 hover 时显示时间的逻辑。

附件卡片使用以下基础类：

```tsx
'flex items-center gap-3 rounded-md border border-border bg-card p-3 flex-shrink-0 cursor-pointer hover:border-foreground/30 hover:bg-secondary transition-colors duration-200'
```

附件尺寸常量和点击、键盘逻辑保持原样。

- [ ] **Step 5: 重构计划、Markdown 和工具标签样式**

PlanPanel 根节点使用：

```tsx
<div className={cn('runner-panel', className)}>
```

展开内容使用 `bg-secondary rounded-md`，进度数字使用 `font-meta`，完成图标使用 `text-emphasis`。

`markdown-content.tsx` 保持 Markdown 解析逻辑不变，只执行以下语义替换：

```text
text-gray-900 / text-gray-800 / text-gray-700 -> text-foreground
text-gray-600 / text-gray-500 -> text-muted-foreground
bg-gray-100 -> bg-secondary
border-gray-200 -> border-emphasis
text-blue-600 -> text-emphasis
rounded -> rounded-sm
rounded-md -> rounded-md
```

工具标签根类改为：

```tsx
className="font-meta inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-2.5 py-1 text-xs text-foreground w-fit max-w-full min-w-0 cursor-pointer hover:border-emphasis hover:bg-accent transition-colors duration-200"
```

- [ ] **Step 6: 运行契约测试与 ESLint**

```bash
cd ui && node --test tests/runner-style-contract.test.mjs && npm run lint
```

Expected: 所有契约测试通过；ESLint 退出码为 0。

- [ ] **Step 7: 提交对话与执行记录样式**

```bash
git add ui/tests/runner-style-contract.test.mjs ui/src/app/globals.css ui/src/components/session-detail-view.tsx ui/src/components/session-header.tsx ui/src/components/chat-message.tsx ui/src/components/attachments-message.tsx ui/src/components/plan-panel.tsx ui/src/components/markdown-content.tsx ui/src/components/tool-use/tool-badge.tsx
git commit -m "style: reshape Runner conversation timeline"
```

### Task 5: 重构文件、工具与 VNC 预览

**Files:**

- Modify: `ui/tests/runner-style-contract.test.mjs`
- Modify: `ui/src/app/globals.css`
- Modify: `ui/src/components/session-detail-view.tsx`
- Modify: `ui/src/components/file-preview-panel.tsx`
- Modify: `ui/src/components/tool-preview-panel.tsx`
- Modify: `ui/src/components/vnc-overlay.tsx`

- [ ] **Step 1: 增加预览界面契约并确认失败**

```js
test('preview surfaces render as flat Runner work windows', () => {
  assert.match(read('src/components/file-preview-panel.tsx'), /runner-preview-shell/)
  assert.match(read('src/components/tool-preview-panel.tsx'), /runner-preview-shell/)
  assert.match(read('src/components/tool-preview-panel.tsx'), /runner-panel-header/)
  assert.match(read('src/components/vnc-overlay.tsx'), /runner-vnc-control/)
})
```

Run:

```bash
cd ui && node --test tests/runner-style-contract.test.mjs
```

Expected: 新增测试失败，4 个预览标记均不存在。

- [ ] **Step 2: 添加预览共享类**

在 `globals.css` 的 `@layer components` 中加入：

```css
.runner-preview-shell {
    border-left: 1px solid var(--foreground);
    background: var(--card);
}

.runner-vnc-control {
    border: 1px solid rgb(255 255 255 / 28%);
    border-radius: 6px;
    background: rgb(0 0 0 / 72%);
    box-shadow: 4px 4px 0 rgb(0 0 0 / 28%);
}
```

- [ ] **Step 3: 将文件和工具预览改为平面窗口**

FilePreviewPanel 根节点改为：

```tsx
<div className="runner-preview-shell flex h-full flex-col">
```

头部改为 `runner-panel-header`，文件图标保持蓝色语义但使用 `rounded-sm`，图片预览使用 `rounded-md border-border`。

ToolPreviewPanel 根节点改为：

```tsx
<div className="runner-preview-shell flex h-full flex-col overflow-hidden">
```

其 Header 改为：

```tsx
<div className="runner-panel-header flex flex-col gap-2 px-4 py-3 flex-shrink-0">
```

工具描述标签使用 `font-meta rounded-md border-border bg-secondary text-xs`。终端、浏览器、搜索、文件、MCP、A2A 和 Skill 的渲染分支与内容保持原样。

在 `session-detail-view.tsx` 中将工具预览外层的 `py-2 pr-2` 删除，使其与文件预览一样贴合右侧区域。文件和工具预览两个外层的 inline style 均使用以下值，防止窄视口溢出；拖动宽度逻辑和动画保持原样：

```tsx
style={{ width: rightPanelWidth, maxWidth: '100vw' }}
```

- [ ] **Step 4: 重构 VNC 状态层和控制按钮**

错误状态容器改为：

```tsx
<div className="runner-vnc-control flex flex-col items-center gap-3 px-10 py-8">
```

连接成功后的退出按钮使用：

```tsx
className="runner-vnc-control inline-flex items-center gap-2 px-5 py-2 text-sm text-white/90 hover:bg-black/80 transition-colors duration-200 cursor-pointer"
```

保持 VNCViewer、连接状态判断、Escape 关闭和回调逻辑不变。

- [ ] **Step 5: 运行契约测试与 ESLint**

```bash
cd ui && node --test tests/runner-style-contract.test.mjs && npm run lint
```

Expected: 所有契约测试通过；ESLint 退出码为 0。

- [ ] **Step 6: 提交预览界面样式**

```bash
git add ui/tests/runner-style-contract.test.mjs ui/src/app/globals.css ui/src/components/session-detail-view.tsx ui/src/components/file-preview-panel.tsx ui/src/components/tool-preview-panel.tsx ui/src/components/vnc-overlay.tsx
git commit -m "style: flatten Runner preview workspaces"
```

### Task 6: 重构设置与 Trace 工作台

**Files:**

- Modify: `ui/tests/runner-style-contract.test.mjs`
- Modify: `ui/src/app/globals.css`
- Modify: `ui/src/components/manus-settings.tsx`
- Modify: `ui/src/components/skill-settings.tsx`
- Modify: `ui/src/components/trace-panel.tsx`

- [ ] **Step 1: 增加设置与 Trace 契约并确认失败**

```js
test('settings and trace use Runner workbench shells', () => {
  assert.match(read('src/components/manus-settings.tsx'), /runner-settings-shell/)
  assert.match(read('src/components/skill-settings.tsx'), /runner-source-panel/)
  assert.match(read('src/components/trace-panel.tsx'), /runner-trace-shell/)
  assert.match(read('src/components/trace-panel.tsx'), /runner-metric/)
})
```

Run:

```bash
cd ui && node --test tests/runner-style-contract.test.mjs
```

Expected: 新增测试失败，设置、源码和 Trace 标记均不存在。

- [ ] **Step 2: 添加设置和 Trace 共享类**

在 `globals.css` 的 `@layer components` 中加入：

```css
.runner-settings-shell,
.runner-trace-shell {
    border: 1px solid var(--foreground);
    border-radius: 6px;
    background: var(--card);
    box-shadow: 8px 8px 0 var(--runner-hard-shadow);
}

.runner-source-panel {
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--secondary);
}

.runner-metric {
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--background);
}

.runner-settings-footer {
    border-top: 1px dashed var(--border);
    background: var(--background);
}
```

- [ ] **Step 3: 重构设置窗口样式**

ManusSettings 的主 DialogContent 改为：

```tsx
<DialogContent className="runner-settings-shell max-h-[calc(100dvh-2rem)] overflow-hidden !max-w-[850px]">
```

主 Header 使用 `runner-panel-header -mx-6 -mt-6 px-6 py-4`。中间主体只做响应式展示调整，使用以下类，所有 `activeSetting` 判断保持原样：

```tsx
<div className="flex min-h-0 flex-col gap-3 sm:flex-row sm:gap-4">
<div className="w-full border-b border-border pb-3 sm:max-w-[180px] sm:border-b-0 sm:pb-0">
<div className="grid grid-cols-2 gap-1 sm:flex sm:flex-col">
<Separator orientation="vertical" className="hidden sm:block" />
<div className="min-h-0 flex-1 h-[min(500px,calc(100dvh-300px))] scrollbar-hide overflow-y-auto">
```

当前菜单继续使用 `default` variant，非当前菜单继续使用 `ghost` variant。

所有 FieldLegend 的 `text-gray-700` 改为 `text-foreground`；FieldDescription 改为 `text-muted-foreground`；列表 Item 使用 `border-border bg-card`。陶土橙只应用于焦点环、Switch、Badge 和选中状态，不改变字段值或校验。

底部 DialogFooter 使用 `runner-settings-footer -mx-6 -mb-6 px-6 py-4`，保留取消、保存和 loading 行为。

SkillSettings 的源码 ScrollArea 改为：

```tsx
<ScrollArea className="runner-source-panel h-[460px]">
```

其余列表和子 Dialog 只使用全局语义颜色，不改上传、启用、删除和读取逻辑。

- [ ] **Step 4: 重构 Trace 控制台样式**

Trace DialogContent 增加 `runner-trace-shell`，内部根节点使用 `bg-card`。Header 使用 `runner-panel-header`，四个指标容器统一为：

```tsx
<div className="runner-metric min-w-0 p-2 font-meta">
```

Trace 列表按钮使用：

```tsx
className={`min-w-0 rounded-md border p-2 text-left text-xs transition-colors duration-200 hover:bg-secondary ${
  detail?.trace_id === trace.trace_id
    ? 'border-emphasis bg-secondary'
    : 'border-border bg-card'
}`}
```

Span 详情的 `pre` 使用 `font-meta rounded-md border border-neutral-700 bg-neutral-950 text-neutral-100`。拖动分隔条、尺寸 state、数据请求和选择逻辑保持不变。

- [ ] **Step 5: 运行契约测试与 ESLint**

```bash
cd ui && node --test tests/runner-style-contract.test.mjs && npm run lint
```

Expected: 所有契约测试通过；ESLint 退出码为 0。

- [ ] **Step 6: 提交设置与 Trace 样式**

```bash
git add ui/tests/runner-style-contract.test.mjs ui/src/app/globals.css ui/src/components/manus-settings.tsx ui/src/components/skill-settings.tsx ui/src/components/trace-panel.tsx
git commit -m "style: finish Runner settings and trace surfaces"
```

### Task 7: 生产构建与多视口视觉验收

**Files:**

- Verify only: `ui/`
- Temporary screenshots: `/tmp/mooc-manus-runner-1440.png`
- Temporary screenshots: `/tmp/mooc-manus-runner-1024.png`
- Temporary screenshots: `/tmp/mooc-manus-runner-390.png`

- [ ] **Step 1: 运行全部可用的自动检查**

```bash
cd ui
node --test tests/runner-style-contract.test.mjs
npm run lint
npm run build
```

Expected: 契约测试全部通过；ESLint 退出码为 0；Next.js 生产构建成功。

说明：仓库现有 `src/lib/ui-layout.test.ts` 使用 `vitest`，但 `package.json` 没有安装或配置 `vitest`。本次计划禁止新增依赖，因此不修改该测试基础设施；在最终报告中明确记录这一既存限制。

- [ ] **Step 2: 启动本地开发服务器**

先确认端口：

```bash
lsof -nP -iTCP:3100 -sTCP:LISTEN
```

Expected: 无输出。如果已有进程，顺延使用 `3101`，不终止用户进程。

启动：

```bash
cd ui && npm run dev -- --hostname 127.0.0.1 --port 3100
```

Expected: Next.js 输出 `Ready`，访问地址为 `http://127.0.0.1:3100`。不设置或修改 `.env`。

- [ ] **Step 3: 生成三个真实浏览器截图**

在另一个终端运行：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1440,1000 --screenshot=/tmp/mooc-manus-runner-1440.png http://127.0.0.1:3100/
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1024,900 --screenshot=/tmp/mooc-manus-runner-1024.png http://127.0.0.1:3100/
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=390,844 --screenshot=/tmp/mooc-manus-runner-390.png http://127.0.0.1:3100/
```

Expected: 三个 PNG 文件均存在且大小大于 20 KB。

- [ ] **Step 4: 逐张检查视觉验收项**

使用 `view_image` 检查三个截图，逐项确认：

```text
首页首屏可见暖白、炭黑、陶土橙、衬线问候和点阵纹理
侧栏、新建任务和当前会话状态层级清楚
输入框、按钮、推荐问题没有文字裁切
1024px 下主内容不与侧栏重叠
390px 下没有横向滚动、文字遮挡或按钮溢出
点阵纹理不覆盖正文和输入内容
焦点、禁用、加载和错误状态仍有可辨识颜色
```

如果本地 API 已运行，再通过现有入口检查会话、工具预览、设置和 Trace；如果 API 未运行，检查首页、设置触发按钮、侧栏错误状态，并用代码契约与生产构建覆盖其余无数据表面。不得伪造会话数据或新增预览路由。

- [ ] **Step 5: 检查源码边界和工作区状态**

```bash
git diff --check
git diff 3e22733 -- ui/src/hooks ui/src/lib/api ui/src/providers ui/src/lib/session-events.ts
git status --short
```

Expected: `git diff --check` 无输出；受保护的功能目录无差异；`git status --short` 无输出。

- [ ] **Step 6: 汇总验收证据**

最终报告必须列出：

```text
样式契约测试结果
ESLint 结果
生产构建结果
三个视口截图路径和观察结论
是否能连接本地 API 并检查数据态界面
既存 vitest 缺失限制
本地开发服务器 URL
```

验收任务不产生代码提交；若前六个任务均已提交且所有检查通过，工作区保持干净。
