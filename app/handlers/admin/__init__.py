from aiogram import Router

from .root import router as root_router
from .students import router as students_router
from .create_student import router as create_student_router
from .lessons_add import router as lessons_add_router
from .student_delete import router as student_delete_router

router = Router()
router.include_router(root_router)
router.include_router(students_router)
router.include_router(create_student_router)
router.include_router(lessons_add_router)
router.include_router(student_delete_router)
