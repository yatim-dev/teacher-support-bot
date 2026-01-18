from . import start, menu, admin, student, parent

routers = [
    start.router,
    menu.router,
    admin.router,
    student.router,
    parent.router,
]
