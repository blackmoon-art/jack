import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

np.random.seed(42)
X = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
true_w, true_b = 2.0, 3.0
y = true_w * X + true_b + np.random.normal(0, 2.0, size=len(X))

n = len(X)
x_mean = np.mean(X)
y_mean = np.mean(y)

numerator = np.sum((X - x_mean) * (y - y_mean))
denominator = np.sum((X - x_mean)**2)
w_hat = numerator / denominator
b_hat = y_mean - w_hat * x_mean

y_pred = w_hat * X + b_hat
residuals = y - y_pred
sse = np.sum(residuals**2)
r2 = 1 - np.sum(residuals**2) / np.sum((y - y_mean)**2)

fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
fig.suptitle('Minimizing SSE = \u2211(y\u1d62 - \u0177\u1d62)\u00b2  (Ordinary Least Squares)', fontsize=16, fontweight='bold', y=1.02)

ax1 = axes[0]
ax1.scatter(X, y, color='#E74C3C', s=80, zorder=5, label='Data points (x\u1d62, y\u1d62)')
ax1.plot(X, y_pred, color='#3498DB', linewidth=2.5, label=f'Fitted line: y = {w_hat:.2f}x + {b_hat:.2f}')
x_line = np.array([0, 11])
y_true_line = true_w * x_line + true_b
ax1.plot(x_line, y_true_line, '--', color='#2ECC71', alpha=0.5, linewidth=1.5, label=f'True line: y = {true_w}x + {true_b}')
for i in range(n):
    ax1.plot([X[i], X[i]], [y[i], y_pred[i]], color='#E74C3C', linewidth=1.2, alpha=0.6)
ax1.set_xlabel('X (Independent variable)', fontsize=12)
ax1.set_ylabel('Y (Dependent variable)', fontsize=12)
ax1.set_title(f'\u2460 Data & Fitted Line\n\u0177 = {w_hat:.2f}x + {b_hat:.2f}', fontsize=13)
ax1.legend(fontsize=9, loc='upper left')
ax1.set_xlim(0, 11)
ax1.grid(alpha=0.2)

ax2 = axes[1]
ax2.bar(range(1, n+1), residuals**2, color='#E74C3C', alpha=0.7, width=0.6, edgecolor='#C0392B')
ax2.set_xlabel('Data point index i', fontsize=12)
ax2.set_ylabel('Squared residual (y\u1d62 - \u0177\u1d62)\u00b2', fontsize=12, color='#E74C3C')
ax2.set_title(f'\u2461 Squared Residuals per Point\nSSE = \u2211(y\u1d62 - \u0177\u1d62)\u00b2 = {sse:.2f}', fontsize=13)
ax2.set_xticks(range(1, n+1))
ax2.grid(alpha=0.2, axis='y')
ax2.text(0.5, 0.95, f'SSE = {sse:.2f}', transform=ax2.transAxes, fontsize=14,
         fontweight='bold', ha='center', va='top', color='#C0392B',
         bbox=dict(facecolor='white', edgecolor='#C0392B', alpha=0.8, boxstyle='round,pad=0.3'))

ax3 = axes[2]
w_range = np.linspace(w_hat - 3, w_hat + 3, 100)
b_range = np.linspace(b_hat - 5, b_hat + 5, 100)
W, B = np.meshgrid(w_range, b_range)
SSE_grid = np.zeros_like(W)
for i in range(len(w_range)):
    for j in range(len(b_range)):
        y_pred_grid = W[j, i] * X + B[j, i]
        SSE_grid[j, i] = np.sum((y - y_pred_grid)**2)

levels = np.linspace(SSE_grid.min(), SSE_grid.max(), 20)
contour = ax3.contourf(W, B, SSE_grid, levels=levels, cmap='YlOrRd', alpha=0.8)
plt.colorbar(contour, ax=ax3, label='SSE (Sum of Squared Errors)', shrink=0.8)
ax3.contour(W, B, SSE_grid, levels=10, colors='gray', alpha=0.3, linewidths=0.5)
ax3.scatter(w_hat, b_hat, color='#2ECC71', s=200, marker='*', zorder=10,
            edgecolors='white', linewidth=2, label=f'Optimal\nw={w_hat:.2f}, b={b_hat:.2f}')
ax3.set_xlabel('Slope w', fontsize=12)
ax3.set_ylabel('Intercept b', fontsize=12)
ax3.set_title(f'\u2462 Loss Landscape (SSE)\n\u2605 = Minimum (SSE={sse:.2f})', fontsize=13)
ax3.legend(fontsize=9)
ax3.grid(alpha=0.1)

plt.tight_layout()
plt.savefig('ols_explanation.png', dpi=150, bbox_inches='tight')
print(f"w = {w_hat:.4f}, b = {b_hat:.4f}")
print(f"SSE = {sse:.4f}, R² = {r2:.4f}")
print("DONE")
