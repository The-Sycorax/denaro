# denaro/consensus.py
"""
Versioned Consensus Rules Management System

This module implements a professional, scalable system for managing blockchain
consensus rules across different versions/forks. It allows for clean separation
of concerns and easy addition of new consensus versions.
"""

import hashlib
from decimal import Decimal
from typing import List, Union, Callable, Dict, Any
from dataclasses import dataclass
from enum import IntEnum
from abc import ABC, abstractmethod


class ConsensusVersion(IntEnum):
    """
    Consensus versions in chronological order.
    Each version represents a set of consensus rules.
    """
    CONSENSUS_V1 = 1
    #CONSENSUS_V2 = 2 # Reserved for future upgrades


@dataclass
class ConsensusActivation:
    """
    Defines when a consensus version becomes active.
    """
    version: ConsensusVersion
    activation_height: int
    description: str
    is_hard_fork: bool  # True if incompatible with previous rules
    
    def is_active(self, block_height: int) -> bool:
        """Check if this consensus version is active at given height."""
        return block_height >= self.activation_height


class ConsensusSchedule:
    """
    Manages the activation schedule for consensus versions.
    This is the ONLY place where activation heights should be defined.
    """
    
    def __init__(self):
        self._activations: List[ConsensusActivation] = []
        self._initialize_schedule()
    
    def _initialize_schedule(self):
        """
        Define the consensus upgrade schedule.
        
        IMPORTANT: This is the single source of truth for all consensus changes.
        Modify this method to update activation heights before deployment.
        """
        self._activations = [
            ConsensusActivation(
                version=ConsensusVersion.CONSENSUS_V1,
                activation_height=0,
                description="Security fixes: proper Merkle trees, MTP timestamps, improved difficulty, coinbase validation",
                is_hard_fork=False
            ),
        ]
        
        # Validate schedule (must be in order)
        for i in range(1, len(self._activations)):
            if self._activations[i].activation_height <= self._activations[i-1].activation_height:
                raise ValueError(f"Consensus activations must be in chronological order")
    
    def get_active_version(self, block_height: int) -> ConsensusVersion:
        """
        Determine which consensus version is active at a given block height.
        """
        active_version = ConsensusVersion.CONSENSUS_V1
        
        for activation in self._activations:
            if activation.is_active(block_height):
                active_version = activation.version
            else:
                break  # Activations are in order, so we can stop
        
        return active_version
    
    def get_activation_height(self, version: ConsensusVersion) -> int:
        """Get the activation height for a specific version."""
        for activation in self._activations:
            if activation.version == version:
                return activation.activation_height
        raise ValueError(f"Unknown consensus version: {version}")
    
    def is_hard_fork(self, version: ConsensusVersion) -> bool:
        """Check if a version is a hard fork."""
        for activation in self._activations:
            if activation.version == version:
                return activation.is_hard_fork
        return False
    
    def get_all_activations(self) -> List[ConsensusActivation]:
        """Get all scheduled consensus activations."""
        return self._activations.copy()


# Global consensus schedule instance
CONSENSUS_SCHEDULE = ConsensusSchedule()


class BaseConsensusRules(ABC):
    """
    Abstract base class defining the interface for consensus rules.
    It also contains rules common across all versions (soft forks).
    """

    @property
    @abstractmethod
    def version(self) -> ConsensusVersion:
        """The consensus version this class implements."""
        pass

    @abstractmethod
    def calculate_merkle_tree(self, transactions: List[Union[Any, str]]) -> str:
        """Calculate the Merkle tree root for a list of transactions."""
        pass

    @abstractmethod
    async def validate_timestamp(
        self,
        content_time: int,
        block_id: int,
        last_timestamp: int,
        current_time: int,
        get_median_time_past_func: Callable = None) -> bool:
        """Validate a block's timestamp."""
        pass

    @abstractmethod
    def calculate_new_difficulty(
        self,
        time_ratio: Decimal,
        current_difficulty: Decimal,
        legacy_hashrate_func: Callable = None) -> Decimal:
        """Calculate the new block difficulty."""
        pass

    @abstractmethod
    def validate_coinbase_transactions(self, transactions: List[Any]) -> bool:
        """Validate the coinbase transactions within a block."""
        pass

    def validate_field_ranges(self, random_value: int, difficulty: Decimal) -> bool:
        """
        Validate block field ranges (soft fork - all versions).
        """
        if random_value < 0 or random_value > 0xFFFFFFFF:
            print(f"Block rejected: random value out of range")
            return False
        
        if difficulty < 0 or difficulty > 6553.5:
            print(f"Block rejected: difficulty out of range")
            return False
        
        return True


class Consensus_V1(BaseConsensusRules):
    """
    Encapsulates all consensus rules for V1 (Security improvements and bug fixes).
    """
    @property
    def version(self) -> ConsensusVersion:
        return ConsensusVersion.CONSENSUS_V1
    
    def calculate_merkle_tree(self, transactions: List[Union[Any, str]]) -> str:
        """Proper binary Merkle tree implementation."""
        tx_hashes = []
        for tx in transactions:
            if isinstance(tx, str):
                tx_hashes.append(tx)
            else:
                tx_hashes.append(tx.hash())
        
        if not tx_hashes:
            return hashlib.sha256(b'').hexdigest()
        
        current_level = sorted(tx_hashes)
        
        while len(current_level) > 1:
            next_level = []
            
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                
                combined = left + right
                parent_hash = hashlib.sha256(combined.encode('utf-8')).hexdigest()
                next_level.append(parent_hash)
            
            current_level = next_level
        
        return current_level[0]

    async def validate_timestamp(
        self,
        content_time: int,
        block_id: int,
        last_timestamp: int,
        current_time: int,
        get_median_time_past_func: Callable = None) -> bool:
        """Median Time Past (MTP) timestamp validation."""
        if get_median_time_past_func is None:
            raise ValueError("MTP function required for V1+ timestamp validation")
        
        median_time = await get_median_time_past_func(block_id - 1)
        
        if content_time <= median_time:
            print(f"Block rejected: timestamp {content_time} not greater than MTP {median_time}")
            return False
        
        if content_time > current_time + 60:  # Stricter future limit
            print(f"Block rejected: timestamp too far in future")
            return False
        
        return True
    
    def calculate_new_difficulty(
        self,
        time_ratio: Decimal,
        current_difficulty: Decimal,
        legacy_hashrate_func: Callable = None) -> Decimal:
        """Improved difficulty calculation with better clamping."""
        from .constants import START_DIFFICULTY
        
        ratio = max(Decimal('0.5'), min(time_ratio, Decimal('2.0')))
        new_difficulty = current_difficulty * ratio
        new_difficulty = max(START_DIFFICULTY, new_difficulty)
        
        return round(new_difficulty, 1)
    
    def validate_coinbase_transactions(self, transactions: List[Any]) -> bool:
        """V1 validation: Forbids coinbase transactions in regular tx list."""
        from .transactions import CoinbaseTransaction
        
        coinbase_count = sum(
            1 for tx in transactions 
            if isinstance(tx, CoinbaseTransaction)
        )
        
        if coinbase_count > 0:
            print(f"Block rejected: {coinbase_count} coinbase in regular txs")
            return False
        
        return True


class ConsensusEngine:
    """
    Main interface for consensus rule enforcement.
    Automatically selects the correct rules based on block height.
    """
    
    def __init__(self, schedule: ConsensusSchedule = None):
        self.schedule = schedule or CONSENSUS_SCHEDULE
        self._rules_cache: Dict[ConsensusVersion, BaseConsensusRules] = {}
        self._rules_map: Dict[ConsensusVersion, type[BaseConsensusRules]] = {
            ConsensusVersion.CONSENSUS_V1: Consensus_V1,
        }
    
    def get_rules(self, block_height: int) -> BaseConsensusRules:
        """
        Get the appropriate consensus rules for a given block height.
        This acts as a factory for consensus rule objects.
        """
        version = self.schedule.get_active_version(block_height)
        
        if version not in self._rules_cache:
            rules_class = self._rules_map.get(version)
            if not rules_class:
                raise NotImplementedError(f"Consensus rules for version {version} not implemented.")
            self._rules_cache[version] = rules_class()
        
        return self._rules_cache[version]
    
    def is_hard_fork_active(self, block_height: int, fork_version: ConsensusVersion) -> bool:
        """
        Check if a specific hard fork is active at given height.
        """
        active_version = self.schedule.get_active_version(block_height)
        return active_version >= fork_version


# Global consensus engine instance
CONSENSUS_ENGINE = ConsensusEngine()


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_consensus_info() -> Dict[str, Any]:
    """
    Get information about all consensus versions and their activation heights.
    Useful for network coordination and debugging.
    """
    info = {
        "current_version": str(ConsensusVersion.CONSENSUS_V1),
        "activations": []
    }
    
    for activation in CONSENSUS_SCHEDULE.get_all_activations():
        info["activations"].append({
            "version": str(activation.version),
            "height": activation.activation_height,
            "description": activation.description,
            "is_hard_fork": activation.is_hard_fork
        })
    
    return info


async def get_median_time_past(database: Any, block_id: int, lookback: int = 11) -> int:
    """
    Calculate median timestamp of last N blocks.
    Used for MTP timestamp validation.
    """
    if block_id < lookback:
        lookback = max(1, block_id)
    
    start_id = max(1, block_id - lookback + 1)
    timestamps = []
    
    for i in range(start_id, block_id + 1):
        block = await database.get_block_by_id(i)
        if block:
            timestamps.append(block['timestamp'])
    
    if not timestamps:
        return 0
    
    timestamps.sort()
    return timestamps[len(timestamps) // 2]