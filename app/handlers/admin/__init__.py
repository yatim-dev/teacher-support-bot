from aiogram import Router

from .root import router as root_router
from .students import router as students_router
from .create_student import router as create_student_router
from .lessons_add import router as lessons_add_router
from .student_delete import router as student_delete_router
from .subscription import router as subscription_router
from .board import router as board_router
from .lessons import router as admin_lessons_router
from .homeworks import router as admin_homeworks_router
from .payments import router as admin_payments_router

router = Router()
router.include_router(root_router)
router.include_router(students_router)
router.include_router(create_student_router)
router.include_router(lessons_add_router)
router.include_router(student_delete_router)
router.include_router(subscription_router)
router.include_router(board_router)
router.include_router(admin_lessons_router)
router.include_router(admin_homeworks_router)
router.include_router(admin_payments_router)