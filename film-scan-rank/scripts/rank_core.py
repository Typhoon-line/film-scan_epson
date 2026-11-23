"""rank_core.py —— 底片扫描排名核心库（图像原语 + 排名引擎）

合并自 film_core.py（读图/指标/对齐几何）与 rank_engine.py（朝向判定/逐格流程/V 型图）。
两者的 standalone main()（旧口径历史入口）已删除；正式管线请运行：
  rank_jpg.py   JPG 全量+加测（0.70/0.15/0.15）
  rank_tiff.py  TIFF 全量+加测（0.85/0.15/0.00）

对外主要接口：
  imread_any / gray / OPS / edge_sharpness / gamut_volume / span_metrics  (图像与指标)
  center_crop / detect_orientation / process_cand / make_vchart          (排名流程)
  ROOT / GROUPS / N / KEYS / PARTNER / OP_DISPLAY / 权重常量              (配置)

铁律：对齐只算几何、从原图直接裁窗，绝不对算指标的图像做 warp（插值会低估锐度）。
朝向以 Pearson 原生扫描为地面真值，SIFT 单应只用于估对齐质量。
"""
import cv2, numpy as np, os
from PIL import Image, ImageDraw, ImageFont

CH, CW = 4000, 6000  # 共同裁窗 H x W


IMG_EXT = ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp')


def imread_any(p):
    """读图 -> BGR, **保留原始位深**(8bit JPEG -> uint8, 48bit TIFF -> uint16)。

    2026-10-18 新增: 为支持 16bit/通道 TIFF(48bit 彩色)。注意必须用 IMREAD_UNCHANGED,
    否则 OpenCV 会静默把 16bit 压成 8bit(>>8), 高光余量全丢 —— 这正是要避免的。
    """
    ext = os.path.splitext(p)[1].lower()
    if ext in ('.tif', '.tiff'):
        im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if im is None:
            raise IOError(f"无法解码 TIFF: {p}")
        if im.ndim == 2:
            im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
        elif im.shape[2] == 4:
            im = cv2.cvtColor(im, cv2.COLOR_BGRA2BGR)
        return im
    im = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise IOError(f"无法解码图像: {p}")
    return im


def to_u8(x):
    """任意位深 -> uint8(显示/写盘/JPEG 用)。16bit 按 >>8, 与 OpenCV 惯例一致。"""
    if x.dtype == np.uint8:
        return x
    if x.dtype == np.uint16:
        return (x >> 8).astype(np.uint8)
    return np.clip(x, 0, 255).astype(np.uint8)


def imread_safe(p):
    """兼容旧调用: 一律返回 8bit BGR(用于缩略图/接触表/写盘)。"""
    return to_u8(imread_any(p))


def gray(im):
    """灰度 -> float32, 统一归一到 0..255 量级(16bit 除以 257, 不截断)。"""
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) if im.ndim == 3 else im
    if g.dtype == np.uint16:
        return g.astype(np.float32) / 257.0
    return g.astype(np.float32)


def gray8(im):
    """SIFT 专用: 必须是 uint8。"""
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) if im.ndim == 3 else im
    return to_u8(g)


def pearson(a, b):
    """灰度相关. 用作对齐质量门限(阈值 0.85): 相关过低说明候选裁窗与参考未对齐好。"""
    a = a.astype(np.float64).ravel(); b = b.astype(np.float64).ravel()
    if a.std() < 1e-6 or b.std() < 1e-6: return 0.0
    return float(np.corrcoef(a, b)[0, 1])


# ---------- 朝向(只用来选方向, 不影响像素选择) ----------
OPS = {
    'identity':      lambda im: im,
    'rot90':         lambda im: cv2.rotate(im, cv2.ROTATE_90_CLOCKWISE),
    'rot180':        lambda im: cv2.rotate(im, cv2.ROTATE_180),
    'rot270':        lambda im: cv2.rotate(im, cv2.ROTATE_90_COUNTERCLOCKWISE),
    'mirror':        lambda im: cv2.flip(im, 1),
    'mirror_rot90':  lambda im: cv2.rotate(cv2.flip(im, 1), cv2.ROTATE_90_CLOCKWISE),
    'mirror_rot180': lambda im: cv2.rotate(cv2.flip(im, 1), cv2.ROTATE_180),
    'mirror_rot270': lambda im: cv2.rotate(cv2.flip(im, 1), cv2.ROTATE_90_COUNTERCLOCKWISE),
}
OP_ORDER = list(OPS.keys())


def best_orientation_by_sift(ref_bgr, cand_bgr):
    """在 8 向里选 SIFT 内点最多的方向"""
    ref_g = gray8(ref_bgr)
    cand_g = gray8(cand_bgr)
    DS = 4
    rds = cv2.resize(ref_g, (ref_g.shape[1] // DS, ref_g.shape[0] // DS))
    try:
        sift = cv2.SIFT_create(nfeatures=4000)
        norm = cv2.NORM_L2
    except Exception:
        sift = cv2.ORB_create(nfeatures=4000)
        norm = cv2.NORM_HAMMING
    kp1, des1 = sift.detectAndCompute(rds, None)
    if des1 is None or len(kp1) < 4:
        return 'identity', {}
    best_op, best_n = 'identity', -1
    scores = {}
    for name in OP_ORDER:
        oriented = OPS[name](cand_g)
        if oriented.shape[:2] != rds.shape:
            oriented = cv2.resize(oriented, (rds.shape[1], rds.shape[0]))
        kp2, des2 = sift.detectAndCompute(oriented, None)
        if des2 is None or len(kp2) < 4:
            scores[name] = 0; continue
        try:
            knn = cv2.BFMatcher(norm).knnMatch(des1, des2, k=2)
        except Exception:
            scores[name] = 0; continue
        # knnMatch 的单条结果可能是不足 2 个元素的列表, 直接 `for m,n in knn` 会解包失败
        good = [p[0] for p in knn if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
        scores[name] = len(good)
        if len(good) > best_n:
            best_n = len(good); best_op = name
    return best_op, {k: int(v) for k, v in scores.items()}


# ---------- 单应: oriented (cand 经朝向变换后) → ref ----------
def sift_homography(ref_bgr, oriented_bgr):
    """返回 H: 把 oriented 像素映射到参考坐标; 在 DS=4 降采样空间算后映射回原图坐标"""
    ref_g = gray8(ref_bgr)
    cand_g = gray8(oriented_bgr)
    DS = 4
    rds = cv2.resize(ref_g, (ref_g.shape[1] // DS, ref_g.shape[0] // DS))
    ods = cv2.resize(cand_g, (cand_g.shape[1] // DS, cand_g.shape[0] // DS))
    sift = cv2.SIFT_create(nfeatures=8000)
    kp1, des1 = sift.detectAndCompute(rds, None)
    kp2, des2 = sift.detectAndCompute(ods, None)
    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        return np.eye(3), 0, 0.0
    bf = cv2.BFMatcher(cv2.NORM_L2)
    knn = bf.knnMatch(des1, des2, k=2)
    # knnMatch 的单条结果可能不足 2 个元素, 直接 `for m,n in knn` 会解包失败
    good = [p[0] for p in knn if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 4:
        return np.eye(3), len(good), 0.0
    src = np.array([kp1[m.queryIdx].pt for m in good], dtype=np.float32) * DS
    dst = np.array([kp2[m.trainIdx].pt for m in good], dtype=np.float32) * DS
    H, inl = cv2.findHomography(dst, src, cv2.RANSAC, 5.0)  # dst(oriented)→src(ref)
    if H is None:
        return np.eye(3), 0, 0.0
    inl = inl.ravel().astype(bool)
    return H, int(inl.sum()), float(inl.sum()) / max(1, len(good))


def rotation_angle_deg(H):
    """从 H 近似估计旋转角(度). 假设近刚体变换."""
    return float(np.degrees(np.arctan2(H[1, 0], H[0, 0])))


# ---------- 几何: 参考框 → oriented 上的 AABB → 原图(im)上的裁窗 ----------
def aabb_in_oriented(H, ref_box, oriented_shape):
    """
    H: oriented → ref. ref_box: (y0,x0,y1,x1) 参考图中的 6000x4000.
    返回 oriented 中的 AABB: (y0,x0,y1,x1)
    """
    Hinv = np.linalg.inv(H)
    y0, x0, y1, x1 = ref_box
    corners_ref = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)
    corners_ref_h = np.hstack([corners_ref, np.ones((4, 1), dtype=np.float32)])
    corners_o = (Hinv @ corners_ref_h.T).T
    corners_o = corners_o[:, :2] / corners_o[:, 2:3]
    xmin, ymin = corners_o.min(axis=0)
    xmax, ymax = corners_o.max(axis=0)
    H_o, W_o = oriented_shape
    x0c = max(0, int(np.floor(xmin)))
    y0c = max(0, int(np.floor(ymin)))
    x1c = min(W_o, int(np.ceil(xmax)))
    y1c = min(H_o, int(np.ceil(ymax)))
    return (y0c, x0c, y1c, x1c)


def box_in_original(op_name, box, im_shape):
    """
    把 oriented 坐标系下的 box(左闭右开) 还原到 im 原图坐标系.
    由于 OPS 是规则的像素置换(旋转/镜像, 无插值), 这里是纯坐标变换.
    im_shape = (H_im, W_im). 返回 (y0, x0, y1, x1) in im coords(可直接做 numpy 切片).

    修正(2026-10-17): 原实现对 rot90/180/270、mirror、mirror_rot90、mirror_rot180
    六向的坐标映射写错(行列互换/符号错), 导致竖构图候选被裁到错误/转置区域.
    以下公式经切片还原测试 8/8 通过.
    """
    yo0, xo0, yo1, xo1 = box
    h, w = im_shape
    mapping = {
        "identity": (yo0, xo0, yo1, xo1),
        "rot90": (h - xo1, yo0, h - xo0, yo1),
        "rot270": (xo0, w - yo1, xo1, w - yo0),
        "rot180": (h - yo1, w - xo1, h - yo0, w - xo0),
        "mirror": (yo0, w - xo1, yo1, w - xo0),
        "mirror_rot90": (h - xo1, w - yo1, h - xo0, w - yo0),
        "mirror_rot180": (h - yo1, xo0, h - yo0, xo1),
        "mirror_rot270": (xo0, yo0, xo1, yo1),
    }
    try:
        return mapping[op_name]
    except KeyError:
        raise ValueError(f"不支持的朝向: {op_name}") from None


# 2026-11-24 修正: mirror_rot90 / mirror_rot270 都是**自逆**(R90∘F 与 R270∘F 各复合两次=identity),
# 旧表误写成两者互逆, 导致 point_in_original 在 154 个测试点里错 152 个。
# 已用「每像素唯一值 + 8 向像素集合」验证: 改自逆后 154/154 全对。
INV = {
    'identity': 'identity', 'rot90': 'rot270', 'rot270': 'rot90', 'rot180': 'rot180',
    'mirror': 'mirror', 'mirror_rot90': 'mirror_rot90',
    'mirror_rot270': 'mirror_rot270', 'mirror_rot180': 'mirror_rot180',
}


def point_in_original(op_name, xo, yo, oriented_shape, im_shape):
    """将 oriented 坐标系下的单点 (xo,yo) 精确还原到 im 原图像素坐标 (xim,yim)。
    通过 OpenCV 旋转/镜像的精确逆运算得到, 不依赖手写坐标公式。"""
    marker = np.zeros((oriented_shape[0], oriented_shape[1]), np.uint8)
    marker[yo, xo] = 1
    inv = OPS[INV[op_name]](marker)
    ys, xs = np.where(inv > 0)
    if len(xs) == 0:
        return None
    return (int(xs[0]), int(ys[0]))


# ---------- 指标(在原图裁出的 6000x4000 上算) ----------
def edge_sharpness(g):
    """对比度归一化边缘锐度 (median-ratio, 内容无关)。2026-10-18 定版恢复此口径。

    曾短暂改用"高频能量占比"(hf/broad, sh=10/sb=80): 动态范围大但对内容敏感
    (内容偏软的帧被误判不锐), 且超线性夸大好组差距(Q edge=135 vs A=100)。
    经用户拍板回退本口径 —— 与 rank_jpg.json 既有基准一致。
    """
    base = cv2.GaussianBlur(g, (0, 0), 18)
    detail = g - base
    gx = cv2.Sobel(detail, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(detail, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx ** 2 + gy ** 2)
    contrast = np.abs(g - cv2.GaussianBlur(g, (0, 0), 15))
    contrast = np.clip(contrast, 1e-3, None)
    ratio = grad / contrast
    ratio = ratio[ratio > 0]
    return float(np.median(ratio))


def gamut_volume(bgr):
    """Lab 色域体积 = 16^3 量化直方图占据的格子数。

    修正(2026-10-17): cv2.COLOR_BGR2LAB 对 8 位输入输出的 a/b 通道**已带 +128 偏移**
    (0=最负,128=中性灰,255=最正)。旧实现又 +128 再 /255, 把整条 a/b 轴压进 8~15 这 ~8 个
    量化格, 严重损失色彩区分度(实测色域被低估 ~86%) 且非线性, 直接污染占 45% 权重的 gamut_norm。
    这里改为直接用已编码的 8 位通道 *q//256 量化为 4 位索引, 打包后用 np.unique 计数 ——
    既修正偏置, 又顺带消除了旧版 set(zip(tolist())) 在千万级像素上的内存/CPU 开销。
    """
    bgr = to_u8(bgr)  # 16bit TIFF 先降到 8bit: 色域按 16 级/轴量化, 8bit 精度足够
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    L, a, bb = cv2.split(lab)
    q = 16
    L_bin = (L.astype(np.uint16) * q // 256).astype(np.uint8)
    a_bin = (a.astype(np.uint16) * q // 256).astype(np.uint8)
    b_bin = (bb.astype(np.uint16) * q // 256).astype(np.uint8)
    packed = ((L_bin.astype(np.uint16) << 8)
              | (a_bin.astype(np.uint16) << 4)
              | b_bin.astype(np.uint16))
    return int(np.unique(packed).size)


def span_metrics(bgr):
    x = bgr.astype(np.float32)
    if bgr.dtype == np.uint16:
        x *= (1.0 / 257.0)          # 归一到 0..255, 与 8bit 数据同尺度可比
    r, g, b = cv2.split(x)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return float(np.percentile(lum, 99.9) - np.percentile(lum, 0.1)), \
           float(np.percentile(lum, 0.1)), float(np.percentile(lum, 99.9))


FONT = r'C:/Windows/Fonts/msyh.ttc'

# 总分权重（2026-11-15 定版口径）：跨度(动态范围)对 16bit TIFF 区分度低，不计入总分。
# 旧 0.40/0.45/0.15 口径已作废——由正式 JSON 反推证实实际生效为 0.85/0.15/0.00。
# rank_tiff.py 主脚本自带同名常量并自行计算 total_norm；本常量供引擎 standalone 跑时口径一致。
W_EDGE, W_GAMUT, W_SPAN = 0.85, 0.15, 0.00

def _font(sz):
    """中文字体; 非 Windows / 字体缺失时回退到 PIL 默认字体, 不阻断出图。"""
    try:
        return ImageFont.truetype(FONT, sz)
    except Exception:
        try:
            return ImageFont.load_default(size=sz)
        except (TypeError, AttributeError):
            return ImageFont.load_default()

# 扫描数据根目录: 优先环境变量 FILM_SCAN_ROOT; 默认本技能目录下的 scans/
# (与 README / SKILL / scans/README 的约定一致)。
# Windows 本机若数据在别处: set FILM_SCAN_ROOT=D:\A-scan\V700test
# Linux/macOS: export FILM_SCAN_ROOT=/absolute/path/to/scans
ROOT = os.environ.get('FILM_SCAN_ROOT') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scans')
GROUPS = [('A', 'A_原玻璃_原片夹_0'),
          ('B', 'B_原玻璃_原片夹垫高_1.0'),
          ('C', 'C_原玻璃_无片夹_0'),
          ('D', 'D_AR玻璃_原片夹_0'),
          ('E', 'E_AR玻璃_原片夹垫高_1.0'),
          ('F', 'F_AR玻璃_超薄片夹_0'),
          ('G', 'G_AR玻璃改_超薄片夹_0'),
          ('H', 'H_AR玻璃改_超薄片夹_0.25'),
          ('I', 'I_AR玻璃改_超薄片夹_0.5'),
          ('J', 'J_AR玻璃改_超薄片夹_0.75')]
N = 6
KEYS = [g[0] for g in GROUPS]   # A, B, C, D, E, F, G, H
PARTNER = {'identity': 'rot180', 'rot180': 'identity', 'rot90': 'rot270', 'rot270': 'rot90',
           'mirror': 'mirror_rot180', 'mirror_rot180': 'mirror',
           'mirror_rot90': 'mirror_rot270', 'mirror_rot270': 'mirror_rot90'}

# 朝向 -> 中文显示
# 注意: 实测参考 A 为横向(landscape, 约 4112×6720, W>H); 故 rot90/270 表示「原生为竖构图、
# 需旋转 90° 对齐到横向参考」; mirror* = 翻转。下面显示名据此标注, 不强行称"纵向"。
OP_DISPLAY = {
    'identity':      '原向',
    'rot90':         '旋转90°',
    'rot180':        '旋转180°',
    'rot270':        '旋转270°',
    'mirror':        '水平翻转',
    'mirror_rot90':  '翻转+旋转90°',
    'mirror_rot180': '翻转+旋转180°',
    'mirror_rot270': '翻转+旋转270°',
}


def imwrite_safe(path, img):
    """绕过 cv2.imwrite 在 Windows 中文路径下静默失败的坑: imencode -> open('wb')。"""
    ext = os.path.splitext(path)[1].lower() or '.jpg'
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    with open(path, 'wb') as f:
        f.write(buf.tobytes())
    return True


def center_crop(im):
    h, w = im.shape[:2]
    y0 = h // 2 - CH // 2
    x0 = w // 2 - CW // 2
    y0 = max(0, min(y0, h - CH))
    x0 = max(0, min(x0, w - CW))
    return im[y0:y0 + CH, x0:x0 + CW]


def frame_path(key, idx):
    folder = dict(GROUPS)[key]
    return os.path.join(ROOT, folder, f"img{idx:03d}.jpg")


def _pearson_best_op(ref_bgr, cand_bgr):
    """Pearson 原生朝向扫描(地面真值): 对 8 向枚举直接比对参考中心裁窗, 取相关最高者。
    对暗/条纹/模糊内容比 SIFT 稳健 —— 已用 ZA/ZB 实测验证: SIFT 给错向(rot180),
    Pearson 正确识别 identity(rot180 相关为负, 明确排除)。"""
    PW, PH = 750, 500
    def _prep(im):
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) if im.ndim == 3 else im
        ch, cw = g.shape[:2]
        y0 = max(0, ch // 2 - PH // 2); x0 = max(0, cw // 2 - PW // 2)
        g = g[y0:y0 + PH, x0:x0 + PW]
        return cv2.resize(g, (PW, PH)).ravel().astype(float)
    ref_s = _prep(ref_bgr)
    best_op, best_r = 'identity', -9.0
    for op, fn in OPS.items():
        try:
            r = float(np.corrcoef(_prep(fn(cand_bgr)), ref_s)[0, 1])
        except Exception:
            r = -9.0
        if r > best_r:
            best_r, best_op = r, op
    return best_op, best_r


def detect_orientation(ref_bgr, cand_bgr):
    """方向验证: 朝向以 Pearson 原生扫描(地面真值)为准 —— 暗/条纹/模糊内容下 SIFT 不可靠,
    已实测 ZA/ZB 其给出相反朝向; SIFT 单应仅用于估对齐质量(Hm/inl_n/angle)。
    抽离自 process_cand, 供「组内共识方向」预扫描复用。"""
    rh, rw = ref_bgr.shape[:2]
    if cand_bgr.shape[:2] != (rh, rw):
        sx = cand_bgr.shape[1] / rw
        sy = cand_bgr.shape[0] / rh
        cand_match = cv2.resize(cand_bgr, (rw, rh))
    else:
        sx = sy = 1.0
        cand_match = cand_bgr
    # 朝向以 Pearson 原生扫描为准(地面真值, 比 SIFT 稳健)
    best_op, pear_r = _pearson_best_op(ref_bgr, cand_match)
    oriented = OPS[best_op](cand_match)
    Hm, inl_n, inl_ratio = sift_homography(ref_bgr, oriented)
    angle = rotation_angle_deg(Hm)
    if abs(angle) > 45:
        partner = PARTNER[best_op]
        oriented_p = OPS[partner](cand_match)
        Hp, inl_p, ratio_p = sift_homography(ref_bgr, oriented_p)
        if inl_p > inl_n:
            best_op, oriented, Hm, inl_n, inl_ratio, angle = (
                partner, oriented_p, Hp, inl_p, ratio_p, rotation_angle_deg(Hp))
    return dict(best_op=best_op, inl_n=inl_n, angle=angle, Hm=Hm,
                oriented=oriented, sx=sx, sy=sy, pearson_op_r=round(float(pear_r), 3))


def process_cand(ref_bgr, cand_bgr, consensus_op=None, good_count=0, use_consensus=False):
    """候选相对参考对齐, 返回指标字典 + 原图裁窗(crop_final) + 旋转到参考方向的裁窗(crop_aligned)。

    use_consensus: 是否启用「组内共识方向」机制(仅 TIFF 管线跑过 orientation_pre_pass 后传 True)。
    JPG 管线从未做预扫描, 保持 False 走原本的单应裁窗路径, 分数不受本次兜底修正影响。

    跨分辨率修正(2026-11-04): 当候选与原图分辨率不同, 先在匹配分辨率估单应, 再映射回原生坐标,
    最终裁窗仍在候选原生像素上取(无 warp, 不违反"不插值算指标"铁律)。

    组内共识方向(2026-11-05)新增: 当该组有 >=4 张方向验证通过(内点>=30), 本帧若「方向异常」
    (内点<30 或 检测朝向不等于组内共识朝向), 则忽略本帧异常、强制采用组内共识朝向取中心裁窗
    (=组内平均位置), 不再依赖本帧弱单应; 标记 consensus=True。退化裁窗兜底同时尊重朝向。
    """
    rh, rw = ref_bgr.shape[:2]
    y0 = rh // 2 - CH // 2
    x0 = rw // 2 - CW // 2
    y0 = max(0, min(y0, rh - CH))
    x0 = max(0, min(x0, rw - CW))
    ref_box = (y0, x0, y0 + CH, x0 + CW)

    det = detect_orientation(ref_bgr, cand_bgr)
    best_op = det['best_op']; inl_n = det['inl_n']; angle = det['angle']
    Hm = det['Hm']; sx = det['sx']; sy = det['sy']

    # consensus override: 方向异常且组内>=4张验证通过 -> 采用共识方向, 不信任本帧弱单应
    used_consensus = False
    if use_consensus and consensus_op is None:
        # 2026-11-24 修正兜底: 启用共识机制但无可靠共识(取向分歧, 或**全部帧判向失败**),
        # 一律退回原生中心裁窗(identity)。旧写法要求 good_count>=4, 全组判向失败时两个分支
        # 都不进, 会拿内点仅个位数的弱单应去裁窗, 造成错位与分数虚高。
        # 指标(edge/gamut/span)对旋转不变, 且中心裁窗=组内平均位置。
        best_op = 'identity'
        used_consensus = True
    elif use_consensus and good_count >= 4:
        if (inl_n < 30) or (best_op != consensus_op):
            best_op = consensus_op
            used_consensus = True

    oriented_native = OPS[best_op](cand_bgr)
    if used_consensus:
        # 组内共识方向 + 中心裁窗(=组内平均位置), 不依赖本帧弱单应
        crop_final = center_crop(oriented_native)
        degenerate = True
    else:
        # 把匹配分辨率下的单应映射回候选原生像素坐标(尺度变换只作用在坐标上, 不改像素)
        if sx != 1.0 or sy != 1.0:
            D = np.diag([1.0 / sx, 1.0 / sy, 1.0])
            H = Hm @ D
        else:
            H = Hm

        box_oriented = aabb_in_oriented(H, ref_box, oriented_native.shape[:2])
        box_im = box_in_original(best_op, box_oriented, cand_bgr.shape[:2])
        yi0, xi0, yi1, xi1 = box_im
        yi0 = max(0, yi0); xi0 = max(0, xi0)
        yi1 = min(cand_bgr.shape[0], yi1); xi1 = min(cand_bgr.shape[1], yi1)
        crop_aabb = cand_bgr[yi0:yi1, xi0:xi1]
        ch, cw = crop_aabb.shape[:2]
        degenerate = False
        if ch >= CH and cw >= CW:
            yc = (ch - CH) // 2
            xc = (cw - CW) // 2
            crop_final = crop_aabb[yc:yc + CH, xc:xc + CW]
        else:
            # AABB 退化(单应退化时可能出现空/极小裁窗)或不足标准尺寸: 退回共识方向下 center_crop(尊重朝向)
            degenerate = True
            crop_final = center_crop(oriented_native)

    edge = edge_sharpness(gray(crop_final))
    gamut = gamut_volume(crop_final)
    span, _, _ = span_metrics(crop_final)

    # 对齐质量(Pearson)与报告朝向 op: 对 8 个朝向取与参考相关最大者(仅显示/门限用; 指标对旋转不变)
    ref_crop = center_crop(ref_bgr)
    _ph, _pw = 500, 750
    ref_small = cv2.resize(gray(ref_crop), (_pw, _ph)).ravel().astype(float)
    best_corr = -2.0
    best_op_r = 'identity'
    for op_try, fn_try in OPS.items():
        ca_try = cv2.resize(gray(fn_try(crop_final)), (_pw, _ph)).ravel().astype(float)
        c = float(np.corrcoef(ref_small, ca_try)[0, 1])
        if c > best_corr:
            best_corr = c
            best_op_r = op_try
    crop_aligned = OPS[best_op_r](crop_final) if best_op_r != 'identity' else crop_final

    info = dict(edge=edge, gamut=gamut, span=span, pearson=round(float(best_corr), 4),
                inliers=int(inl_n), op=best_op_r, angle=round(float(0.0 if used_consensus else angle), 2),
                degenerate=degenerate, consensus=used_consensus)
    return info, crop_final, crop_aligned


def make_vchart(avg, group_keys, path):
    """V 型焦点模型图: 纵轴=总分, 横轴=A/B/C/D...文件夹序号; 甜点区映射在 X 轴(总分落在最好成绩-容差内的文件夹纵向绿色列), 最好成绩用橙色明示。PIL+雅黑渲染中文。"""
    W, H = 920, 540
    ml, mr, mt, mb = 80, 40, 70, 75
    pw, ph = W - ml - mr, H - mt - mb
    img = Image.new('RGB', (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    totals = [avg[k]['total'] for k in group_keys]
    best = max(totals)
    ymin = min(min(totals), 100.0) - 8
    ymax = max(totals) + 8
    tol = 10.0  # 甜点区容差(向下)

    def X(i):
        return ml + int((i + 0.5) * pw / len(group_keys))

    def Y(v):
        return mt + int((ymax - v) / (ymax - ymin) * ph)

    slot = pw / len(group_keys)

    # 甜点区: 映射到 X 轴 —— 总分落在 [best-tol, best] 的文件夹, 画纵向绿色列(覆盖整图区高度)
    sweet = [totals[i] >= best - tol for i in range(len(group_keys))]
    for i in range(len(group_keys)):
        if sweet[i]:
            x0 = X(i) - slot / 2
            x1 = X(i) + slot / 2
            draw.rectangle([x0, mt, x1, mt + ph], fill=(205, 235, 205))

    # 坐标轴框 + y 网格
    draw.rectangle([ml, mt, ml + pw, mt + ph], outline=(60, 60, 60), width=2)
    step = 10
    lo = int(np.floor(ymin / step) * step)
    hi = int(np.ceil(ymax / step) * step)
    for v in range(lo, hi + 1, step):
        yy = Y(v)
        if mt <= yy <= mt + ph:
            draw.line([ml, yy, ml + pw, yy], fill=(215, 215, 215))
            draw.text((ml - 40, yy - 8), str(v), fill=(70, 70, 70), font=_font(16))

    # 点 + 折线 + x 标签
    pts = [(X(i), Y(totals[i])) for i in range(len(group_keys))]
    for j in range(len(pts) - 1):
        draw.line([pts[j], pts[j + 1]], fill=(90, 120, 200), width=2)
    for i, (x, y) in enumerate(pts):
        is_best = (totals[i] == best)
        col = (255, 140, 0) if is_best else (30, 130, 220)  # 最好成绩橙色, 其余蓝
        r = 9 if is_best else 7
        draw.ellipse([x - r, y - r, x + r, y + r], fill=col)
        draw.text((x - 22, y - 22), f"{totals[i]:.1f}", fill=(30, 30, 30), font=_font(16))
        draw.text((x - 12, mt + ph + 12), group_keys[i], fill=(40, 40, 40), font=_font(22))

    # 标题 + 图例
    draw.text((ml, 12), "V 型焦点模型：总分 vs 文件夹", fill=(20, 20, 20), font=_font(26))
    draw.rectangle([W - 260, 16, W - 260 + 18, 34], fill=(150, 200, 150))
    draw.text((W - 260 + 24, 14), "甜点区", fill=(40, 40, 40), font=_font(16))
    draw.ellipse([W - 95, 18, W - 95 + 14, 32], fill=(255, 140, 0))
    draw.text((W - 75, 14), "最好成绩", fill=(40, 40, 40), font=_font(16))
    img.save(path)


