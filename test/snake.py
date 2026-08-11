#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
五颜六色贪吃蛇 —— 终端版
使用 ANSI 转义序列输出彩色字符，方向键 / WASD 控制，空格暂停，Q / Esc 退出。

修复：
1. 解决闪烁：只在程序启动/退出时进入/恢复 cbreak 终端模式，不再每次按键都切换；
   绘制改为光标归位重绘，避免反复清屏造成的闪烁。
2. 解决操作无反应：统一在 cbreak 模式下非阻塞读取按键，正确解析方向键 ESC 序列。
"""

import os
import sys
import time
import random
import select
import termios
import tty


# ---------- 工具函数：读取单个按键 ----------
def _read_nonblock(timeout):
    """非阻塞地读取指定数量的可用字符（至多 32 字节）。"""
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    if not r:
        return b""
    chunk = os.read(sys.stdin.fileno(), 32)
    return chunk


def get_key(timeout=0.05, _in_escape=False):
    """
    读取一个按键（可能占用多字节，比如方向键的 ESC 序列）。
    调用前必须已进入 cbreak 模式。返回解析后的按键名，无输入返回 None。
    """
    data = _read_nonblock(timeout)
    if not data:
        return None
    # 按键解码，兼容含中文的环境
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")

    if text == "\x1b":
        # 可能是单独的 ESC 键，或方向键序列的开始。给一点时间读后续字节。
        tail = _read_nonblock(0.02)
        if tail:
            try:
                tail = tail.decode("utf-8")
            except UnicodeDecodeError:
                tail = tail.decode("latin-1")
            # ←[ A / B / C / D
            if tail.startswith("[A"):
                return "up"
            if tail.startswith("[B"):
                return "down"
            if tail.startswith("[C"):
                return "right"
            if tail.startswith("[D"):
                return "left"
            # 可能是更多转义，简单丢弃
            return None
        return "esc"
    return text


# ---------- ANSI 颜色 ----------
RESET = "\033[0m"
BOLD = "\033[1m"
# 前景色（fg）和背景色（bg）
FG = {
    'black': 30, 'red': 31, 'green': 32, 'yellow': 33,
    'blue': 34, 'magenta': 35, 'cyan': 36, 'white': 37,
}
BG = {
    'black': 40, 'red': 41, 'green': 42, 'yellow': 43,
    'blue': 44, 'magenta': 45, 'cyan': 46, 'white': 47,
}
# 一组鲜亮的配色方案，用于蛇身
SNAKE_COLORS = ['red', 'green', 'yellow', 'blue', 'magenta', 'cyan']
FOOD_UNICODE = ['🍎', '🍓', '🍇', '🍊', '🍋', '🍒']


def colorize(text, fg=None, bg=None, bold=False):
    codes = []
    if bold:
        codes.append(BOLD)
    if fg:
        codes.append(f"\033[{FG[fg]}m")
    if bg:
        codes.append(f"\033[{BG[bg]}m")
    return "".join(codes) + text + RESET


# ---------- 游戏常量 ----------
WIDTH, HEIGHT = 30, 18
SCORE_PER_FOOD = 10


# ---------- 游戏主体 ----------
class Snake:
    def __init__(self):
        self.reset()

    def reset(self):
        # 初始蛇身（头在中间偏左）
        mid_x, mid_y = WIDTH // 2, HEIGHT // 2
        self.snake = [(mid_x, mid_y), (mid_x - 1, mid_y), (mid_x - 2, mid_y)]
        self.direction = (1, 0)   # 初始向右
        self.next_direction = (1, 0)
        self.food = None
        self.score = 0
        self.game_over = False
        self.alive = True
        self.spawn_food()

    def spawn_food(self):
        while True:
            pos = (random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1))
            if pos not in self.snake:
                self.food = pos
                break

    def turn(self, dir_key):
        # 方向键 / WASD 控制
        mapping = {
            'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0),
            'w': (0, -1), 's': (0, 1), 'a': (-1, 0), 'd': (1, 0),
        }
        new_dir = mapping.get(dir_key)
        if not new_dir:
            return
        # 不能原路掉头
        if (new_dir[0] == -self.direction[0] and new_dir[1] == -self.direction[1]):
            return
        self.next_direction = new_dir

    def step(self):
        """推进一帧。返回是否继续游戏。"""
        self.direction = self.next_direction
        head = self.snake[0]
        new_head = (head[0] + self.direction[0], head[1] + self.direction[1])

        # 撞墙
        if not (0 <= new_head[0] < WIDTH and 0 <= new_head[1] < HEIGHT):
            self.alive = False
            return False

        # 撞自己
        if new_head in self.snake:
            self.alive = False
            return False

        self.snake.insert(0, new_head)

        # 吃食物
        if new_head == self.food:
            self.score += SCORE_PER_FOOD
            self.spawn_food()
        else:
            self.snake.pop()

        return True


def hide_cursor():
    print("\033[?25l", end="", flush=True)


def show_cursor():
    print("\033[?25h", end="", flush=True)


def clear_screen():
    print("\033[2J\033[H", end="", flush=True)


def draw(snake, paused):
    # 光标归位并重绘，不清屏 -> 减少闪烁
    out = ["\033[H"]
    lines = []

    # 顶边框
    top = "╔" + "═" * (WIDTH * 2 + 1) + "╗"
    bottom = "╚" + "═" * (WIDTH * 2 + 1) + "╝"
    lines.append(top)

    # 按坐标构建
    body_set = set(snake.snake)
    head = snake.snake[0]
    food = snake.food

    for y in range(HEIGHT):
        row = "║ "
        for x in range(WIDTH):
            if (x, y) == food:
                row += colorize("★", fg='yellow', bold=True) + " "
            elif (x, y) == head:
                # 蛇头：醒目的白色
                row += colorize("●", fg='white', bold=True, bg='black') + " " if snake.alive else colorize("✖", fg='red', bold=True) + " "
            elif (x, y) in body_set:
                idx = snake.snake.index((x, y))
                color = SNAKE_COLORS[idx % len(SNAKE_COLORS)]
                row += colorize("■", fg=color, bold=True) + " "
            else:
                row += "· "
        row += "║"
        lines.append(row)

    lines.append(bottom)

    # 信息栏
    status = ""
    if not snake.alive:
        status = colorize(" 游戏结束！", fg='red', bold=True)
    elif paused:
        status = colorize(" 已暂停 ", fg='cyan', bold=True)
    lines.append("")
    lines.append(f"  {colorize('得分', fg='magenta', bold=True)}: {colorize(str(snake.score), fg='yellow', bold=True)}"
                 f"    {colorize('长度', fg='cyan', bold=True)}: {colorize(str(len(snake.snake)), fg='green', bold=True)}"
                 f"    {status}")
    lines.append("")
    lines.append(colorize(" 操作: ↑↓←→ / WASD 移动   空格 暂停   Q 退出   R 重新开始", fg='blue'))

    out.append("\n".join(lines))

    # 打印后清掉残留行（避免画面滚动）
    print("".join(out), end="", flush=True)
    print("\033[J", end="", flush=True)  # 从光标处清除到屏幕末尾


def main():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    last_score = 0
    try:
        os.system("")  # Windows 上启用 ANSI

        tty.setcbreak(fd)   # 只在开始时进入一次 cbreak 模式
        hide_cursor()
        clear_screen()

        snake = Snake()
        paused = False
        last_score = snake.score

        draw(snake, paused)

        while True:
            key = get_key()

            if key is not None:
                lk = key
                if isinstance(lk, str):
                    lk = lk.lower()
                if lk == 'q' or lk == 'esc':
                    break
                if lk == ' ':
                    paused = not paused
                elif not paused:
                    if lk in ('w', 'a', 's', 'd', 'up', 'down', 'left', 'right'):
                        snake.turn(lk)

            if not snake.alive:
                draw(snake, paused)
                # 按 r 重新开始，按 q 退出
                rk = None
                while rk is None:
                    rk = get_key(0.3)
                rk = rk.lower() if isinstance(rk, str) else rk
                if rk == 'r':
                    snake.reset()
                    paused = False
                    clear_screen()
                    draw(snake, paused)
                    continue
                elif rk == 'q' or rk == 'esc':
                    break
                # 其他按键忽略，等待有效操作
                snake.alive = False
                continue

            if not paused:
                if not snake.step():
                    draw(snake, paused)
                    continue

            draw(snake, paused)
            time.sleep(0.12)

    except KeyboardInterrupt:
        pass
    finally:
        # 恢复终端
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass
        show_cursor()
        clear_screen()
        print(f"再见！分数: {last_score}")


if __name__ == "__main__":
    main()
