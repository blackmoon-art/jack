import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

np.random.seed(42)
x = np.linspace(0, 10, 20)
y = 2.5 * x + 1 + np.random.randn(20) * 2

A = np.vstack([x, np.ones(len(x))]).T
slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
y_pred = slope * x + intercept

ss_res = np.sum((y - y_pred) ** 2)
ss_tot = np.sum((y - np.mean(y)) ** 2)
r2 = 1 - ss_res / ss_tot

fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

ax1 = axes[0]
ax1.scatter(x, y, color='#FF6B6B', s=100, edgecolors='white', linewidth=1.5, zorder=5, label='Data points')
ax1.plot(x, y_pred, color='#4ECDC4', linewidth=3, zorder=4, label=f'Fitted line: y = {slope:.2f}x + {intercept:.2f}')
for i in range(len(x)):
    ax1.plot([x[i], x[i]], [y[i], y_pred[i]], '--', color='#95A5A6', linewidth=1.2, alpha=0.7)
ax1.set_xlabel('X (Independent variable)', fontsize=12)
ax1.set_ylabel('Y (Dependent variable)', fontsize=12)
ax1.set_title('Linear Regression Fit', fontsize=14, fontweight='bold')
ax1.legend(fontsize=10, loc='upper left')
ax1.grid(True, alpha=0.3)
ax1.set_xlim(-0.5, 10.5)

ax2 = axes[1]
residuals = y - y_pred
ax2.bar(x, residuals, width=0.4, color='#FF6B6B', alpha=0.7, edgecolor='white')
ax2.axhline(y=0, color='#2C3E50', linewidth=1.5)
ax2.set_xlabel('X (Independent variable)', fontsize=12)
ax2.set_ylabel('Residuals', fontsize=12)
ax2.set_title('Residual Distribution', fontsize=14, fontweight='bold')
ax2.grid(True, alpha=0.3)
ax2.set_xlim(-0.5, 10.5)

plt.suptitle(f'Linear Regression - R\u00b2 = {r2:.3f}', fontsize=16, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig('linear_regression.png', dpi=150, bbox_inches='tight')
print(f"Done! R^2 = {r2:.3f}")
print(f"Equation: y = {slope:.2f}x + {intercept:.2f}")
