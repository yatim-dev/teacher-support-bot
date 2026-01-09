from . import start, menu, admin, lesson_actions, student, parent

routers = [
    start.router,
    menu.router,
    admin.router,
    lesson_actions.router,
    student.router,
    parent.router,
]
