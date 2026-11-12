from datetime import date
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID, uuid4

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field


# ==========================================
# 1. МОДЕЛИ ДАННЫХ И ВАЛИДАЦИЯ
# ==========================================
class TrainerStatus(str, Enum):
    WORKING = "WORKING"
    ON_LEAVE = "ON_LEAVE"
    NOT_WORKING = "NOT_WORKING"


class TrainerBase(BaseModel):
    surname: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=100)
    patronymic: Optional[str] = Field(None, max_length=100)
    phone: str = Field(..., pattern=r"^\+?[0-9\-\s\(\)]{7,20}$")


class TrainerCreate(TrainerBase):
    status: TrainerStatus = TrainerStatus.WORKING


class TrainerUpdate(TrainerBase):
    status: TrainerStatus


class TrainerStatusUpdate(BaseModel):
    status: TrainerStatus


class Trainer(TrainerBase):
    id: UUID
    status: TrainerStatus


class TrainerShort(BaseModel):
    id: UUID
    surname: str
    name: str
    status: TrainerStatus


class ClientBase(BaseModel):
    surname: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=100)
    patronymic: Optional[str] = Field(None, max_length=100)
    birthday: date
    phone: str = Field(..., pattern=r"^\+?[0-9\-\s\(\)]{7,20}$")
    email: EmailStr


class ClientCreate(ClientBase):
    is_active: bool = True
    trainer_id: Optional[UUID] = None


class ClientUpdate(ClientBase):
    is_active: bool
    trainer_id: Optional[UUID] = None


class ClientStatusUpdate(BaseModel):
    is_active: bool


class Client(ClientBase):
    id: UUID
    is_active: bool
    trainer_id: Optional[UUID] = None


class ClientDetail(Client):
    trainer: Optional[TrainerShort] = None


class TrainerDetail(Trainer):
    clients: List[Client] = []


# ==========================================
# 2. СЛОЙ ХРАНЕНИЯ В ПАМЯТИ (REPOSITORIES)
# ==========================================
class InMemoryTrainerRepository:
    def __init__(self):
        self._storage: Dict[UUID, Trainer] = {}

    def save(self, trainer: Trainer) -> Trainer:
        self._storage[trainer.id] = trainer
        return trainer

    def get_by_id(self, trainer_id: UUID) -> Optional[Trainer]:
        return self._storage.get(trainer_id)

    def get_all(self) -> List[Trainer]:
        return list(self._storage.values())


class InMemoryClientRepository:
    def __init__(self):
        self._storage: Dict[UUID, Client] = {}

    def save(self, client: Client) -> Client:
        self._storage[client.id] = client
        return client

    def get_by_id(self, client_id: UUID) -> Optional[Client]:
        return self._storage.get(client_id)

    def get_all(self) -> List[Client]:
        return list(self._storage.values())

    def get_by_trainer_id(self, trainer_id: UUID) -> List[Client]:
        return [c for c in self._storage.values() if c.trainer_id == trainer_id]


# ==========================================
# 3. БИЗНЕС-ЛОГИКА (SERVICE)
# ==========================================
class FitnessService:
    def __init__(self, client_repo: InMemoryClientRepository, trainer_repo: InMemoryTrainerRepository):
        self.client_repo = client_repo
        self.trainer_repo = trainer_repo

    # --- Тренеры ---
    def create_trainer(self, data: TrainerCreate) -> Trainer:
        trainer = Trainer(id=uuid4(), **data.model_dump())
        return self.trainer_repo.save(trainer)

    def update_trainer(self, trainer_id: UUID, data: TrainerUpdate) -> Trainer:
        if not self.trainer_repo.get_by_id(trainer_id):
            raise HTTPException(status_code=404, detail="Тренер не найден")
        updated = Trainer(id=trainer_id, **data.model_dump())
        return self.trainer_repo.save(updated)

    def update_trainer_status(self, trainer_id: UUID, status: TrainerStatus) -> Trainer:
        trainer = self.trainer_repo.get_by_id(trainer_id)
        if not trainer:
            raise HTTPException(status_code=404, detail="Тренер не найден")
        trainer.status = status
        return self.trainer_repo.save(trainer)

    def get_all_trainers(self) -> List[Trainer]:
        return self.trainer_repo.get_all()

    def get_trainer_detail(self, trainer_id: UUID) -> TrainerDetail:
        trainer = self.trainer_repo.get_by_id(trainer_id)
        if not trainer:
            raise HTTPException(status_code=404, detail="Тренер не найден")
        clients = self.client_repo.get_by_trainer_id(trainer_id)
        return TrainerDetail(**trainer.model_dump(), clients=clients)

    # --- Клиенты ---
    def create_client(self, data: ClientCreate) -> Client:
        if data.trainer_id and not self.trainer_repo.get_by_id(data.trainer_id):
            raise HTTPException(status_code=400, detail="Указанный тренер не существует")
        client = Client(id=uuid4(), **data.model_dump())
        return self.client_repo.save(client)

    def update_client(self, client_id: UUID, data: ClientUpdate) -> Client:
        if not self.client_repo.get_by_id(client_id):
            raise HTTPException(status_code=404, detail="Клиент не найден")
        if data.trainer_id and not self.trainer_repo.get_by_id(data.trainer_id):
            raise HTTPException(status_code=400, detail="Указанный тренер не существует")
        updated = Client(id=client_id, **data.model_dump())
        return self.client_repo.save(updated)

    def get_all_clients(self) -> List[Client]:
        return self.client_repo.get_all()

    def get_client(self, client_id: UUID) -> Client:
        client = self.client_repo.get_by_id(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return client

    def get_client_detail(self, client_id: UUID) -> ClientDetail:
        client = self.get_client(client_id)
        trainer_short = None
        if client.trainer_id:
            trainer = self.trainer_repo.get_by_id(client.trainer_id)
            if trainer:
                trainer_short = TrainerShort(
                    id=trainer.id,
                    surname=trainer.surname,
                    name=trainer.name,
                    status=trainer.status,
                )
        return ClientDetail(**client.model_dump(), trainer=trainer_short)

    def update_client_status(self, client_id: UUID, is_active: bool) -> Client:
        client = self.get_client(client_id)
        client.is_active = is_active
        return self.client_repo.save(client)

    def assign_trainer(self, client_id: UUID, trainer_id: UUID) -> Client:
        client = self.get_client(client_id)
        if not self.trainer_repo.get_by_id(trainer_id):
            raise HTTPException(status_code=404, detail="Тренер не найден")
        client.trainer_id = trainer_id
        return self.client_repo.save(client)


# ==========================================
# 4. КОНТРОЛЛЕРЫ (ЭНДПОИНТЫ REST API)
# ==========================================
app = FastAPI(title="Fitness Center Management API — Part 1")
router = APIRouter(prefix="/api")

client_repo = InMemoryClientRepository()
trainer_repo = InMemoryTrainerRepository()
service = FitnessService(client_repo=client_repo, trainer_repo=trainer_repo)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"message": "Ошибка валидации входных данных", "errors": exc.errors()},
    )


# --- Клиенты ---
@router.post("/clients", response_model=Client, status_code=status.HTTP_201_CREATED, tags=["Clients"])
def create_client(data: ClientCreate):
    return service.create_client(data)


@router.put("/clients/{id}", response_model=Client, tags=["Clients"])
def update_client(id: UUID, data: ClientUpdate):
    return service.update_client(id, data)


@router.get("/clients", response_model=List[Client], tags=["Clients"])
def get_clients():
    return service.get_all_clients()


@router.get("/clients/{id}", response_model=Client, tags=["Clients"])
def get_client(id: UUID):
    return service.get_client(id)


@router.get("/clients/{id}/detail", response_model=ClientDetail, tags=["Clients"])
def get_client_detail(id: UUID):
    return service.get_client_detail(id)


@router.patch("/clients/{id}/status", response_model=Client, tags=["Clients"])
def update_client_status(id: UUID, data: ClientStatusUpdate):
    return service.update_client_status(id, data.is_active)


@router.post("/clients/{clientId}/trainer/{trainerId}", response_model=Client, tags=["Clients"])
def assign_trainer(clientId: UUID, trainerId: UUID):
    return service.assign_trainer(clientId, trainerId)


# --- Тренеры ---
@router.post("/trainers", response_model=Trainer, status_code=status.HTTP_201_CREATED, tags=["Trainers"])
def create_trainer(data: TrainerCreate):
    return service.create_trainer(data)


@router.put("/trainers/{id}", response_model=Trainer, tags=["Trainers"])
def update_trainer(id: UUID, data: TrainerUpdate):
    return service.update_trainer(id, data)


@router.patch("/trainers/{id}/status", response_model=Trainer, tags=["Trainers"])
def update_trainer_status(id: UUID, data: TrainerStatusUpdate):
    return service.update_trainer_status(id, data.status)


@router.get("/trainers/{id}/detail", response_model=TrainerDetail, tags=["Trainers"])
def get_trainer_detail(id: UUID):
    return service.get_trainer_detail(id)


@router.get("/trainers", response_model=List[Trainer], tags=["Trainers"])
def get_trainers():
    return service.get_all_trainers()


app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
