from . import start, menu, help, admin, lesson_actions, student, parent

routers = [
    start.router,
    menu.router,
    help.router,
    admin.router,
    lesson_actions.router,
    student.router,
    parent.router,
]
