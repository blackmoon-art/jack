import pygame
import random
import sys

# 初始化
pygame.init()
WIDTH, HEIGHT = 600, 600
CELL = 30
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("🐍 贪吃蛇 - Decisive Cat 出品")
clock = pygame.time.Clock()
font = pygame.font.SysFont("simhei", 36)

# 颜色
BLACK = (0, 0, 0)
GREEN = (0, 200, 0)
RED = (200, 0, 0)
WHITE = (255, 255, 255)

def draw_grid():
    for x in range(0, WIDTH, CELL):
        pygame.draw.line(screen, (40, 40, 40), (x, 0), (x, HEIGHT))
    for y in range(0, HEIGHT, CELL):
        pygame.draw.line(screen, (40, 40, 40), (0, y), (WIDTH, y))

def game():
    snake = [(5, 5)]
    dx, dy = 1, 0
    food = (random.randint(0, WIDTH//CELL-1), random.randint(0, HEIGHT//CELL-1))
    score = 0
    speed = 10
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_UP and dy == 0:
                    dx, dy = 0, -1
                elif event.key == pygame.K_DOWN and dy == 0:
                    dx, dy = 0, 1
                elif event.key == pygame.K_LEFT and dx == 0:
                    dx, dy = -1, 0
                elif event.key == pygame.K_RIGHT and dx == 0:
                    dx, dy = 1, 0

        # 移动蛇头
        head = (snake[0][0] + dx, snake[0][1] + dy)
        
        # 撞墙检测
        if head[0] < 0 or head[0] >= WIDTH//CELL or head[1] < 0 or head[1] >= HEIGHT//CELL:
            break
        
        # 撞自己
        if head in snake:
            break

        snake.insert(0, head)
        
        # 吃食物
        if head == food:
            score += 1
            speed = min(20, 10 + score // 3)
            food = (random.randint(0, WIDTH//CELL-1), random.randint(0, HEIGHT//CELL-1))
            # 确保食物不在蛇身上
            while food in snake:
                food = (random.randint(0, WIDTH//CELL-1), random.randint(0, HEIGHT//CELL-1))
        else:
            snake.pop()

        # 绘制
        screen.fill(BLACK)
        draw_grid()
        
        # 画蛇
        for i, (x, y) in enumerate(snake):
            color = (0, 255 - i*2, 0) if i < 50 else (0, 155, 0)
            pygame.draw.rect(screen, color, (x*CELL+2, y*CELL+2, CELL-4, CELL-4))
        
        # 画食物
        pygame.draw.rect(screen, RED, (food[0]*CELL+2, food[1]*CELL+2, CELL-4, CELL-4))
        
        # 显示分数
        score_text = font.render(f"分数: {score}", True, WHITE)
        screen.blit(score_text, (10, 10))
        
        pygame.display.flip()
        clock.tick(speed)

    # 游戏结束
    screen.fill(BLACK)
    over_text = font.render(f"游戏结束！得分: {score}", True, RED)
    restart_text = font.render("按 R 重新开始 / ESC 退出", True, WHITE)
    screen.blit(over_text, (WIDTH//2 - over_text.get_width()//2, HEIGHT//2 - 40))
    screen.blit(restart_text, (WIDTH//2 - restart_text.get_width()//2, HEIGHT//2 + 10))
    pygame.display.flip()

    waiting = True
    while waiting:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_r:
                    game()
                elif event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()

if __name__ == "__main__":
    game()
