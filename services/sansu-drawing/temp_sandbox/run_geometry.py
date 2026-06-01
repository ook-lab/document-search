
import matplotlib
matplotlib.use('Agg') # GUIバックエンドを無効化
import os
import matplotlib.pyplot as plt

# コード実行のメイン処理
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
fig, ax = plt.subplots()
x = np.linspace(0, 2*np.pi, 100)
line, = ax.plot(x, np.sin(x))
def update(frame):
    line.set_ydata(np.sin(x + frame / 10.0))
    return line,
ani = animation.FuncAnimation(fig, update, frames=20, blit=True)
ani.save('animation.gif', writer='pillow', fps=10)
print('[RESULT_AREA] 10.50')
