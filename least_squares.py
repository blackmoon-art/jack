import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

# macOS 可用中文字体
plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC', 'STHeiti', 'Songti SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

np.random.seed(123)
x = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
true_w, true_b = 2.0, 3.0
y = true_w * x + true_b + np.random.normal(0, 2.5, size=len(x))

# ---------- OLS 计算 ----------
n = len(x)
x_mean = np.mean(x)
y_mean = np.mean(y)

w_hat = np.sum((x - x_mean) * (y - y_mean)) / np.sum((x - x_mean)**2)
b_hat = y_mean - w_hat * x_mean

y_pred = w_hat * x + b_hat
residuals = y - y_pred
sse = np.sum(residuals**2)
mse = sse / n
rmse = np.sqrt(mse)
r2 = 1 - np.sum(residuals**2) / np.sum((y - y_mean)**2)

# ========== 图1：残差可视化 ==========
fig1, axes = plt.subplots(1, 2, figsize=(16, 6.5))
fig1.suptitle('最小二乘法（OLS）—— 残差平方和最小化', fontsize=18, fontweight='bold', y=1.02)

# 左图：拟合直线 + 残差
ax = axes[0]
ax.scatter(x, y, color='#E74C3C', s=100, edgecolors='white', linewidth=1.5, zorder=5, label='数据点 (x_i, y_i)')
ax.plot(x, y_pred, color='#3498DB', linewidth=3, zorder=4, label=f'拟合直线 y_hat = {w_hat:.2f}x + {b_hat:.2f}')

# 画残差线（正残差绿色，负残差红色）
for i in range(n):
    color = '#2ECC71' if residuals[i] >= 0 else '#E74C3C'
    ax.plot([x[i], x[i]], [y[i], y_pred[i]], '--', color=color, linewidth=2, alpha=0.8)
    mid_y = (y[i] + y_pred[i]) / 2
    offset = 0.4 if residuals[i] >= 0 else -0.4
    ax.text(x[i] + 0.15, mid_y, f'{residuals[i]:.2f}', fontsize=9, color=color, fontweight='bold')

ax.set_xlabel('X（自变量）', fontsize=13)
ax.set_ylabel('Y（因变量）', fontsize=13)
ax.set_title('(a) 拟合直线与残差\n最小化所有竖线长度的平方和', fontsize=13, fontweight='bold')
ax.legend(fontsize=10, loc='upper left')
ax.set_xlim(0, 11)
ax.set_ylim(0, 28)
ax.grid(True, alpha=0.2)

# 右图：残差平方柱状图
ax = axes[1]
colors = ['#2ECC71' if r >= 0 else '#E74C3C' for r in residuals]
bars = ax.bar(range(1, n+1), residuals**2, color=colors, alpha=0.75, width=0.6, edgecolor='white', linewidth=1.5)

for i, (bar, val) in enumerate(zip(bars, residuals**2)):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3, f'{val:.2f}',
            ha='center', va='bottom', fontsize=9, fontweight='bold', color='#2C3E50')

ax.set_xlabel('数据点序号 i', fontsize=13)
ax.set_ylabel('残差平方 (y_i - y_hat_i)^2', fontsize=13, color='#E74C3C')
ax.set_title(f'(b) 每个点的残差平方\n求和得 SSE = {sse:.2f}', fontsize=13, fontweight='bold')
ax.set_xticks(range(1, n+1))
ax.grid(True, alpha=0.2, axis='y')

# 突出显示SSE
ax.text(0.5, 0.95, f'SSE = Σ(y_i - y_hat_i)^2 = {sse:.2f}', transform=ax.transAxes, fontsize=16,
        fontweight='bold', ha='center', va='top', color='#C0392B',
        bbox=dict(facecolor='#FFF5F5', edgecolor='#C0392B', alpha=0.9, boxstyle='round,pad=0.5'))

plt.tight_layout()
plt.savefig('least_squares_1.png', dpi=150, bbox_inches='tight')
print("图1 saved")

# ========== 图2：最小二乘公式推导可视化 ==========
fig2, axes = plt.subplots(1, 3, figsize=(18, 6))
fig2.suptitle('最小二乘法公式推导与损失景观', fontsize=18, fontweight='bold', y=1.02)

# 左图：残差平方和随w变化（固定b为最优值）
ax = axes[0]
w_range = np.linspace(w_hat - 3, w_hat + 3, 100)
sse_w = np.array([np.sum((y - (w * x + b_hat))**2) for w in w_range])
ax.plot(w_range, sse_w, color='#3498DB', linewidth=2.5)
ax.axvline(x=w_hat, color='#E74C3C', linestyle='--', linewidth=1.5, alpha=0.7,
           label=f'最优 w = {w_hat:.2f}')
ax.scatter([w_hat], [sse], color='#E74C3C', s=150, zorder=10, marker='*', edgecolors='white', linewidth=2)
ax.set_xlabel('斜率 w', fontsize=13)
ax.set_ylabel('SSE 残差平方和', fontsize=13, color='#E74C3C')
ax.set_title(f'(c) 固定 b={b_hat:.2f}，SSE 随 w 变化\n最低点 -> 最优 w', fontsize=12, fontweight='bold')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.2)

# 中图：残差平方和随b变化（固定w为最优值）
ax = axes[1]
b_range = np.linspace(b_hat - 5, b_hat + 5, 100)
sse_b = np.array([np.sum((y - (w_hat * x + b))**2) for b in b_range])
ax.plot(b_range, sse_b, color='#E67E22', linewidth=2.5)
ax.axvline(x=b_hat, color='#E74C3C', linestyle='--', linewidth=1.5, alpha=0.7,
           label=f'最优 b = {b_hat:.2f}')
ax.scatter([b_hat], [sse], color='#E74C3C', s=150, zorder=10, marker='*', edgecolors='white', linewidth=2)
ax.set_xlabel('截距 b', fontsize=13)
ax.set_ylabel('SSE 残差平方和', fontsize=13, color='#E74C3C')
ax.set_title(f'(d) 固定 w={w_hat:.2f}，SSE 随 b 变化\n最低点 -> 最优 b', fontsize=12, fontweight='bold')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.2)

# 右图：损失景观（w和b同时变化）
ax = axes[2]
w_range2 = np.linspace(w_hat - 3, w_hat + 3, 50)
b_range2 = np.linspace(b_hat - 5, b_hat + 5, 50)
W, B = np.meshgrid(w_range2, b_range2)
SSE_grid = np.zeros_like(W)
for i in range(len(w_range2)):
    for j in range(len(b_range2)):
        y_pred_grid = W[j, i] * x + B[j, i]
        SSE_grid[j, i] = np.sum((y - y_pred_grid)**2)

levels = np.linspace(SSE_grid.min(), SSE_grid.max(), 25)
contour = ax.contourf(W, B, SSE_grid, levels=levels, cmap='YlOrRd', alpha=0.85)
cbar = plt.colorbar(contour, ax=ax, label='SSE 残差平方和', shrink=0.8)
ax.contour(W, B, SSE_grid, levels=15, colors='gray', alpha=0.3, linewidths=0.5)
ax.scatter(w_hat, b_hat, color='#2ECC71', s=250, marker='*', zorder=10,
           edgecolors='white', linewidth=2.5, label=f'* 全局最优\nw={w_hat:.2f}, b={b_hat:.2f}')
ax.set_xlabel('斜率 w', fontsize=13)
ax.set_ylabel('截距 b', fontsize=13)
ax.set_title(f'(e) 完整损失景观 (SSE)\n* 标记全局最小值', fontsize=12, fontweight='bold')
ax.legend(fontsize=10, loc='upper right')
ax.grid(True, alpha=0.1)

plt.tight_layout()
plt.savefig('least_squares_2.png', dpi=150, bbox_inches='tight')
print("图2 saved")

# ========== 图3：公式推导流程图 ==========
fig3, ax = plt.subplots(figsize=(16, 7))
ax.axis('off')

# 绘制推导流程图
steps = [
    ("1. 设定模型", "y_hat_i = w*x_i + b", "假设线性关系"),
    ("2. 定义误差", "e_i = y_i - y_hat_i", "每个点的预测误差"),
    ("3. 平方求和", "SSE = \u03a3(y_i - y_hat_i)^2", "避免正负抵消"),
    ("4. 求偏导数", "\u2202SSE/\u2202w = 0\n\u2202SSE/\u2202b = 0", "凸函数极值条件"),
    ("5. 得到公式", "w = \u03a3(x_i-x)(y_i-y) / \u03a3(x_i-x)^2\nb = y_mean - w*x_mean", "解析解!"),
]

colors_box = ['#3498DB', '#E74C3C', '#F39C12', '#9B59B6', '#2ECC71']
x_positions = np.linspace(0.05, 0.85, len(steps))
y_box = 0.55

for i, (title, formula, desc) in enumerate(steps):
    x = x_positions[i]
    # 框
    rect = plt.Rectangle((x-0.08, y_box-0.2), 0.16, 0.4, facecolor=colors_box[i], alpha=0.15,
                         edgecolor=colors_box[i], linewidth=2.5)
    ax.add_patch(rect)
    # 标题
    ax.text(x, y_box+0.12, title, fontsize=13, fontweight='bold', ha='center', color=colors_box[i])
    # 公式
    ax.text(x, y_box-0.02, formula, fontsize=13, fontweight='bold', ha='center', color='#2C3E50')
    # 描述
    ax.text(x, y_box-0.15, desc, fontsize=10, ha='center', color='#7F8C8D', style='italic')
    # 箭头
    if i < len(steps) - 1:
        ax.annotate('', xy=(x_positions[i+1]-0.08, y_box), xytext=(x+0.08, y_box),
                    arrowprops=dict(arrowstyle='->', color='#BDC3C7', lw=2))

# 底部结果框
ax.text(0.5, 0.12, f'最终结果: y = {w_hat:.2f}x + {b_hat:.2f}    SSE = {sse:.2f}    R^2 = {r2:.3f}',
        fontsize=16, fontweight='bold', ha='center', color='white',
        bbox=dict(facecolor='#2C3E50', edgecolor='#34495E', boxstyle='round,pad=0.6'))

ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.set_title('最小二乘法五步推导流程', fontsize=18, fontweight='bold', pad=20)
plt.tight_layout()
plt.savefig('least_squares_3.png', dpi=150, bbox_inches='tight')
print("图3 saved")

# 输出关键数值
print(f"\n{'='*50}")
print(f"关键结果")
print(f"{'='*50}")
print(f"拟合方程: y = {w_hat:.4f}x + {b_hat:.4f}")
print(f"真实方程: y = {true_w}x + {true_b}")
print(f"SSE (残差平方和): {sse:.4f}")
print(f"MSE (均方误差):   {mse:.4f}")
print(f"RMSE (均方根误差): {rmse:.4f}")
print(f"R^2 (决定系数):    {r2:.4f}")
print(f"数据点数: {n}")
print(f"{'='*50}")
