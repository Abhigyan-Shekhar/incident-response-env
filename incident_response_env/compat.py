from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

try:
    from openenv.core.env_server.http_server import create_app as openenv_create_app
    from openenv.core.env_server.interfaces import Environment as OpenEnvEnvironment
    from openenv.core.env_server.types import (
        Action as OpenEnvAction,
        EnvironmentMetadata,
        Observation as OpenEnvObservation,
        State as OpenEnvState,
    )

    OPENENV_AVAILABLE = True
except ImportError:
    OPENENV_AVAILABLE = False
    openenv_create_app = None

    class OpenEnvAction(BaseModel):
        model_config = ConfigDict(
            extra="forbid",
            validate_assignment=True,
            arbitrary_types_allowed=True,
        )

        metadata: dict[str, Any] = Field(default_factory=dict)

    class OpenEnvObservation(BaseModel):
        model_config = ConfigDict(
            extra="forbid",
            validate_assignment=True,
            arbitrary_types_allowed=True,
        )

        done: bool = False
        reward: float | None = None
        metadata: dict[str, Any] = Field(default_factory=dict)

    class OpenEnvState(BaseModel):
        model_config = ConfigDict(
            extra="allow",
            validate_assignment=True,
            arbitrary_types_allowed=True,
        )

        episode_id: Optional[str] = None
        step_count: int = 0

    class EnvironmentMetadata(BaseModel):
        name: str
        description: str
        version: str = "0.1.0"

    class OpenEnvEnvironment(ABC):
        SUPPORTS_CONCURRENT_SESSIONS = False

        @abstractmethod
        def reset(
            self,
            seed: Optional[int] = None,
            episode_id: Optional[str] = None,
            **kwargs: Any,
        ) -> OpenEnvObservation:
            raise NotImplementedError

        @abstractmethod
        def step(
            self,
            action: OpenEnvAction,
            timeout_s: Optional[float] = None,
            **kwargs: Any,
        ) -> OpenEnvObservation:
            raise NotImplementedError

        @property
        @abstractmethod
        def state(self) -> OpenEnvState:
            raise NotImplementedError

        def get_metadata(self) -> EnvironmentMetadata:
            return EnvironmentMetadata(
                name=self.__class__.__name__,
                description=f"{self.__class__.__name__} environment",
            )

        def close(self) -> None:
            return None
