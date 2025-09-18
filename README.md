# Villgro Backend (Django + DRF)

Backend service for the **Villgro Assessment & Management Tool**.  
This project provides APIs for managing startups (SPOs), Villgro Admins, and Bank users — including questionnaires, assessments, and reports.  

> 🚀 Current scope: Docker setup, Django project skeleton, initial apps, Swagger API docs, and Pytest-based testing framework.  

---

## 🏗 Tech Stack
- **Django 5.x** – Web framework  
- **Django REST Framework** – API layer  
- **PostgreSQL 16** – Database  
- **Docker + Docker Compose** – Containerized setup  
- **drf-spectacular** – OpenAPI/Swagger documentation  
- **pytest + pytest-django** – Testing framework  

---

## 📂 Project Structure
```
villgro-backend/
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
├── requirements.txt
├── pytest.ini
├── manage.py
├── config/                # Django project configs
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py / asgi.py
├── accounts/              # custom user & roles (SPO, ADMIN, BANK_USER)
├── organizations/         # startup orgs & founders
├── banks/                 # banks & access
├── questionnaires/        # sections, questions, conditional logic
└── assessments/           # assessments, answers, scoring
```

---

## ⚙️ Setup & Run

### 1. Clone repo
```bash
git clone <repo-url>
cd villgro-backend
```

### 2. Build containers
```bash
docker-compose build
```

### 3. Start services
```bash
docker-compose up
```

Django will auto-run migrations and start on:  
👉 http://localhost:8000  

---

## 🔑 API Documentation
Swagger UI is available at:  
👉 [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)  

Schema endpoint:  
👉 [http://localhost:8000/api/schema/](http://localhost:8000/api/schema/)  

---

## 🧪 Testing
Run all tests with **pytest**:

```bash
docker-compose run --rm web pytest -v
```

Add coverage:
```bash
docker-compose run --rm web pytest --cov=.
```

---

## 👥 User Roles (as per SOW)
- **SPO (Startup Portal)** – Startup organizations & founders  
- **Admin (Villgro)** – Manages SPOs, Banks, and system setup  
- **Bank User** – Access to assessment reports  

---

## 📌 Next Steps
- Implement **Custom User Model** (`accounts`) with roles  
- Add **Questionnaire models** (Sections, Questions, Logic Rules)  
- Build **Assessment APIs** with scoring & 6-month reassessment rules  
- Add **PDF/Charts reporting** with WeasyPrint / matplotlib  
- Wire in **Celery + Redis** for async tasks (emails, reports)  
