# ROM 图片编辑器

> NDS/GBA ROM 瓦片图片编辑器 (PySide6 重写版)
> 兼容 Var1.0 / Var2.0 / Var3.0 .gpp 工程文件
> 原 VB6 版本作者: SHIKEYU ｜ 重写: Python 3.12 + PySide6

用于编辑 ROM 中的瓦片图形资源。支持从 ROM 中读取、显示、编辑瓦片图片，并将修改后的数据写回 ROM。主要面向 ROM 汉化、美术修改、资源提取等场景。

![主窗口](docs/images/main_window.png)

## 核心功能

- 从 ROM 指定地址读取瓦片图片并显示
- 导入 / 导出 BMP、PNG、JPG 等格式的位图
- 将编辑后的瓦片数据写回 ROM（支持压缩格式写回）
- 在图片上插入文字（支持描边、阴影效果）
- MAP 图片瓦片去重 + 水平 / 垂直翻转匹配
- GPP 工程文件管理（多图片条目）

## 安装与启动

### 环境要求

- Python 3.12+
- PySide6
- Pillow（可选，用于导入 PNG/JPG/TIFF 等格式）

### 安装依赖

```bash
pip install PySide6 Pillow
```

### 启动

```bash
python main.py
```

## 支持的图片格式

| 格式 | 说明 | 瓦片大小 |
|------|------|----------|
| 16色 | 4bit/像素，每瓦片 32 字节 | 8×8 |
| 256色 | 8bit/像素，每瓦片 64 字节 | 8×8 |
| 超16色 | 16色瓦片 + 每瓦片 1 字节调色板基址 | 8×8 |
| PCE16色 | PCE 风格 4 位平面，16×16 | 16×16 |
| PCE16色8x8 | PCE 风格 4 位平面 | 8×8 |
| 16色BMP | 16色，直接 BMP 格式导入导出 | - |
| 256色BMP | 256色，直接 BMP 格式导入导出 | - |
| 16色MAP | 16色 MAP 格式（map 表 + 瓦片表） | 8×8 |
| 256色MAP | 256色 MAP 格式（map 表 + 瓦片表） | 8×8 |

## 支持的压缩格式

| 格式 | 地址写法 | 说明 |
|------|----------|------|
| RAW（无压缩） | `0x偏移` | 直接 ROM 内偏移 |
| LZ77 | `0x块地址;0x解压后偏移` | 分号分隔 |
| RLE | `0x块地址!0x解压后偏移` | 感叹号分隔 |
| Huffman | `0x块地址:0x解压后偏移` | 冒号分隔 |
| Huff+LZ77 | `0xHuff块地址:0xLZ77块地址;0x解压后偏移` | 两级压缩 |

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+N | 新建项目 |
| Ctrl+O | 打开 GPP 工程 |
| Ctrl+S | 保存 GPP 工程 |
| Ctrl+Z | 撤销 |
| Ctrl+Y | 重做 |
| 鼠标滚轮 | 画布缩放 |

## 项目结构

```
ndspicture/
├── main.py                      # 程序入口
├── src/
│   ├── core/
│   │   ├── bmp_io.py             # BMP 导入导出
│   │   ├── compression.py        # LZ77/RLE/Huffman 压缩解压
│   │   ├── crc16.py              # CRC16 校验
│   │   ├── gpp_project.py         # GPP 工程文件读写
│   │   ├── palette.py            # 调色板管理
│   │   ├── picture_editor.py     # 图片编辑核心逻辑
│   │   └── tiles.py              # 瓦片编解码
│   └── ui/
│       ├── canvas.py             # 画布控件
│       └── main_window.py        # 主窗口
├── tests/
│   ├── test_core.py              # 核心单元测试
│   └── test_integration.py       # 集成测试
├── assets/
│   └── icon.jpg                  # 应用图标
└── docs/
    ├── images/
    │   └── main_window.png       # 主窗口截图
    └── 使用说明.md                # 详细使用说明
```

## 文档

详细使用说明请参阅 [使用说明.md](docs/使用说明.md)。

## 致谢

- 原 VB6 版本作者: SHIKEYU
- 重写: Python 3.12 + PySide6
