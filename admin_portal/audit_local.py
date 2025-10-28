import threading
_local = threading.local()

def set_current_request(request): _local.request = request
def get_current_request(): return getattr(_local, "request", None)

def get_actor():
    req = get_current_request()
    user = getattr(req, "user", None)
    if user and user.is_authenticated:
        return user
    return None