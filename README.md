# film-scan_epson

为 **爱普生（Epson）平板扫描仪** 胶片扫描玩家准备的 **skill 教程与参考资料** 集合。

本仓库针对 Epson 旗舰平板（GT-X800 / GT-X970 / V700 / V750 等）在 135 / 120 / 4×5 胶片扫描中的对焦、片夹、垫高、防牛顿环等实操痛点，把验证过的流程沉淀成可复用的 WorkBuddy skill。**后续会持续补充更多 skill。**

## 收录的 skill

### [tilt-focus-sweep](tilt-focus-sweep/) —— 倾斜标靶法计算片夹垫高高度
利用 135 调焦卡（USAF 1951 黑白标靶）长条扫描图，找出扫描仪焦平面落在长条上的最佳位置，算出片夹四个支脚应统一垫起的高度，消除因片夹与玻璃不平行导致的离焦模糊。支持**两遍精测法**，把定位精度提升到约 0.027mm/份。

详见子目录 [`tilt-focus-sweep/README.md`](tilt-focus-sweep/README.md)。

## 如何使用这些 skill
- **作为 WorkBuddy skill 安装**：把对应子目录（如 `tilt-focus-sweep/`）复制到 `~/.workbuddy/skills/` 下，WorkBuddy 会自动识别其中的 `SKILL.md`。
- **独立运行**：各 skill 也可脱离 WorkBuddy，按子目录 README 的命令行说明单独用 `python` 调用。

### 在其他 agent 中安装
本仓库的 skill 子目录（如 `tilt-focus-sweep/`）是标准的「`SKILL.md` + 脚本 + 资源」自包含结构，**原则上可被任何支持加载外部 skill / 插件的 agent 复用**（仓库已挂 `trae` / `openclaw` / `qclaw` / `hermes-agent` 等生态标签）：

1. 下载或克隆本仓库，取出需要的 skill 子目录。
2. 按目标 agent 的官方文档，把该子目录放入其 **skill / 插件 / 扩展目录**（Trae、OpenClaw、QClaw、Hermes 等各自的目录命名不同，以官方说明为准）。
3. 首次使用先装依赖：`pip install -r requirements.txt`，随后按子目录 README 的命令行调用。

> 注意：不同 agent 对 `SKILL.md` 的字段要求略有差异，若某平台无法直接识别，可参照其文档调整 frontmatter（如 `name` / `description`），脚本本身无平台硬编码，跨 agent 通用。

## 许可证
各 skill 以其子目录内的 `LICENSE` 为准（脚本多为 MIT）；操作示意图版权归原作者所有，转载使用前请取得授权。
