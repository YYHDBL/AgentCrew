# AgentCrew 应用图标

图标以三个相互连接的瓷白弧形组成抽象的 A 形标记，表达多名数字员工协作执行任务。深墨蓝底座与克制的边缘高光用于建立清楚的轮廓，缩小后保留三段连接结构和中央留白。图标采用 macOS 圆角方形外轮廓，外部背景具有透明通道。

`agentcrew-icon.png` 为 1024 × 1024 的正式资源，供 Electron Dock、窗口、侧栏品牌与欢迎界面使用。主进程通过 electron-vite 的资源导入机制将 PNG 随构建产物保存，应用启动时使用 Electron 原生 Dock API 设置图标。

`agentcrew.icns` 包含 16、32、128、256、512 像素及对应两倍尺寸的图像表示，使用 macOS `sips` 与 `iconutil` 制作。项目当前尚无应用安装包配置，此资源供 macOS 应用包图标配置使用。

## 生成方式与提示词

图像通过 Codex 内置 imagegen 工具生成，并进行透明边缘整理。正式资源与所有应用引用均保存在当前项目中。

生成提示词：

```text
Use case: logo-brand. Asset type: final production macOS Dock application icon for AgentCrew, a desktop workspace where several AI employees collaborate to execute tasks. Create one polished icon asset, square 1024 x 1024 canvas. Transparent alpha outside the icon. Center a single native macOS continuous-rounded-square tile, approximately 86% of canvas width, with generous transparent margin and a very restrained soft shadow. Orthographic perfectly front-facing composition, no perspective mockup. Tile material: deep midnight ink navy #172033, satin enamel, subtle precision-machined bevel, extremely restrained soft lighting. Central emblem: three thick, elegantly interlocking rounded arch/ribbon pieces forming one distinctive abstract A-shaped mark, bilateral balance and an open triangular negative-space center. The three connected elements suggest a coordinated crew. Emblem in warm porcelain white #F4F5F8 with only a whisper of cool steel-blue edge light. Bold simple silhouette, generous internal spacing, immediately legible at 32px and 64px. Refined macOS desktop software identity, editorial restraint, premium industrial design, crisp geometry, slightly sculptural but mostly flat, impeccable edge quality. No literal people, no robot face, no brain, no sparkles, no circuits, no gears, no letters or words as typography, no extra symbols, no rainbow gradient, no glossy plastic, no heavy shadow, no text, no watermarks. The full surrounding background must be genuinely transparent rather than a checkerboard texture. Output only this one centered app icon, no presentation sheet.
```

## 验证

执行 `npm run typecheck` 与 `npm run build` 核查图标资源的导入和构建。执行 `node tests/brand-icon.integration.mjs` 核查真实 Electron 启动、原生 PNG 解码、透明边缘、ICNS 图像表示、侧栏图片和 favicon 加载，以及正常退出。检查结果保存到已忽略的 `desktop/.artifacts/`。
