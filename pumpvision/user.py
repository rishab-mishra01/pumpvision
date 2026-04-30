import os
from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, user_id: str, role: str):
        self.id = user_id
        self.role = role


def load_user_by_id(user_id: str):
    owner_username = os.environ.get("OWNER_USERNAME", "owner")
    attendant_username = os.environ.get("ATTENDANT_USERNAME", "attendant")

    if user_id == owner_username:
        return User(user_id, "owner")
    if user_id == attendant_username:
        return User(user_id, "attendant")
    return None
