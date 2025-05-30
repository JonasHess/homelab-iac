#!/usr/bin/env python3
"""
Unified Restic Operations Script

This script handles both backup and restore operations for ResticBackup CRDs.
It performs discovery of CRDs and executes the appropriate restic operations.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

class ResticOperationsError(Exception):
    """Custom exception for restic operations errors"""
    pass

class ResticOperations:
    def __init__(self):
        """Initialize the ResticOperations handler"""
        self.setup_logging()
        self.setup_clients()
        self.temp_dir = None
        
    def setup_logging(self):
        """Configure structured logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
            
    def setup_clients(self):
        """Initialize Kubernetes clients"""
        try:
            # Load in-cluster configuration
            try:
                config.load_incluster_config()
                self.logger.info("Loaded in-cluster Kubernetes configuration")
            except Exception:
                # Fallback to local config for development
                config.load_kube_config()
                self.logger.info("Loaded local Kubernetes configuration")
            
            self.custom_api = client.CustomObjectsApi()
            self.core_api = client.CoreV1Api()
            
        except Exception as e:
            self.logger.error(f"Failed to setup Kubernetes clients: {e}")
            raise ResticOperationsError(f"Kubernetes client setup failed: {e}")
    
    def load_global_config(self) -> Dict[str, Any]:
        """Load global configuration from mounted ConfigMaps"""
        try:
            config_file = "/config/global-excludes.yaml"
            if not os.path.exists(config_file):
                self.logger.warning(f"Global config file not found at {config_file}")
                return {"globalExcludes": []}
            
            with open(config_file, 'r') as f:
                data = yaml.safe_load(f)
                global_excludes = data.get('globalExcludes', [])
                self.logger.info(f"Loaded {len(global_excludes)} global exclude patterns")
                return {"globalExcludes": global_excludes}
                
        except Exception as e:
            self.logger.error(f"Failed to load global config: {e}")
            return {"globalExcludes": []}
    
    def discover_crds(self, label_selector: str) -> List[Dict[str, Any]]:
        """Discover ResticBackup CRDs with specified label selector"""
        try:
            
            self.logger.info(f"Discovering CRDs with label selector: {label_selector}")
            
            crds = self.custom_api.list_cluster_custom_object(
                group="backup.homelab.dev",
                version="v1",
                plural="resticbackups",
                label_selector=label_selector
            )
            
            backup_configs = []
            for crd in crds.get('items', []):
                try:
                    config = self.process_backup_crd(crd)
                    if config:
                        backup_configs.append(config)
                except Exception as e:
                    crd_name = crd.get('metadata', {}).get('name', 'unknown')
                    self.logger.error(f"Failed to process CRD {crd_name}: {e}")
                    continue
            
            self.logger.info(f"Successfully processed {len(backup_configs)} CRD configurations")
            return backup_configs
            
        except ApiException as e:
            self.logger.error(f"Kubernetes API error discovering CRDs: {e}")
            raise ResticOperationsError(f"Failed to discover CRDs: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error discovering CRDs: {e}")
            raise ResticOperationsError(f"CRD discovery failed: {e}")
    
    def process_backup_crd(self, crd: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a single ResticBackup CRD"""
        metadata = crd.get('metadata', {})
        spec = crd.get('spec', {})
        
        name = metadata.get('name')
        namespace = spec.get('namespace')
        pvc_name = spec.get('pvcName')
        
        if not all([name, namespace, pvc_name]):
            self.logger.error(f"CRD {name} missing required fields")
            return None
        
        self.logger.info(f"Processing CRD: {name} (PVC: {namespace}/{pvc_name})")
        
        # Resolve PVC to hostPath
        try:
            host_path = self.resolve_pvc_to_hostpath(pvc_name, namespace)
        except Exception as e:
            self.logger.error(f"Failed to resolve PVC {namespace}/{pvc_name}: {e}")
            return None
        
        # Build configuration
        config = {
            "name": name,
            "namespace": namespace,
            "pvcName": pvc_name,
            "path": host_path,
            "include": spec.get('include', []),
            "exclude": spec.get('exclude', []),
        }
        
        # Optional fields
        for field in ['excludeLargerThan', 'excludeCaches', 'excludeIfPresent']:
            if field in spec:
                config[field] = spec[field]
        
        self.logger.info(f"Successfully configured: {name} -> {host_path}")
        return config
    
    def resolve_pvc_to_hostpath(self, pvc_name: str, namespace: str) -> str:
        """Resolve PVC to PV hostPath via Kubernetes API"""
        try:
            # Get PVC
            pvc = self.core_api.read_namespaced_persistent_volume_claim(
                name=pvc_name, namespace=namespace
            )
            
            pv_name = pvc.spec.volume_name
            if not pv_name:
                raise ResticOperationsError(f"PVC {namespace}/{pvc_name} not bound to PV")
            
            # Get PV
            pv = self.core_api.read_persistent_volume(name=pv_name)
            
            if pv.spec.host_path and pv.spec.host_path.path:
                return pv.spec.host_path.path
            else:
                raise ResticOperationsError(f"PV {pv_name} missing hostPath")
                
        except ApiException as e:
            raise ResticOperationsError(f"K8s API error resolving {namespace}/{pvc_name}: {e}")
        except Exception as e:
            raise ResticOperationsError(f"Error resolving {namespace}/{pvc_name}: {e}")
    
    def run_restic_command(self, args: List[str], timeout: int = 3600) -> Tuple[bool, str, str]:
        """Execute a restic command with proper error handling"""
        try:
            self.logger.info(f"Executing: restic {' '.join(args)}")
            
            result = subprocess.run(
                ["restic"] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False
            )
            
            success = result.returncode == 0
            if success:
                self.logger.info("Restic command completed successfully")
            else:
                self.logger.error(f"Restic command failed with code {result.returncode}")
                self.logger.error(f"Error output: {result.stderr}")
            
            return success, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Restic command timed out after {timeout} seconds")
            return False, "", "Command timed out"
        except Exception as e:
            self.logger.error(f"Failed to execute restic command: {e}")
            return False, "", str(e)
    
    def initialize_repository(self) -> bool:
        """Initialize restic repository if it doesn't exist"""
        self.logger.info("Checking/initializing restic repository...")
        
        # Check if repository exists
        success, stdout, stderr = self.run_restic_command(["snapshots", "--json"])
        if success:
            self.logger.info("Repository already initialized")
            return True
        
        # Initialize repository
        self.logger.info("Initializing new repository...")
        success, stdout, stderr = self.run_restic_command(["init"])
        if success:
            self.logger.info("Repository initialized successfully")
            return True
        else:
            self.logger.error(f"Failed to initialize repository: {stderr}")
            return False
    
    def build_filter_files(self, item: Dict[str, Any], global_excludes: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """Build include and exclude filter files for a backup item"""
        if not self.temp_dir:
            self.temp_dir = tempfile.mkdtemp()
        
        item_name = item['name']
        
        # Create exclude file
        exclude_file = os.path.join(self.temp_dir, f"exclude-{item_name}.txt")
        with open(exclude_file, 'w') as f:
            # Global excludes first
            for pattern in global_excludes:
                f.write(f"{pattern}\n")
            # Item-specific excludes
            for pattern in item.get('exclude', []):
                f.write(f"{pattern}\n")
        
        # Create include file if includes are specified
        include_file = None
        if item.get('include'):
            include_file = os.path.join(self.temp_dir, f"include-{item_name}.txt")
            with open(include_file, 'w') as f:
                for pattern in item['include']:
                    f.write(f"{item['path']}/{pattern}\n")
        
        return include_file, exclude_file if os.path.getsize(exclude_file) > 0 else None
    
    def execute_backup(self, items: List[Dict[str, Any]], global_excludes: List[str]) -> Dict[str, bool]:
        """Execute backup operations for all items"""
        self.logger.info(f"Starting backup execution for {len(items)} items")
        
        if not self.initialize_repository():
            raise ResticOperationsError("Failed to initialize repository")
        
        results = {}
        today_tag = f"backup-{datetime.now().strftime('%Y%m%d')}"
        
        for item in items:
            item_name = item['name']
            item_path = item['path']
            
            self.logger.info(f"Backing up {item_name}: {item_path}")
            
            # Check if path exists
            if not os.path.exists(item_path):
                self.logger.error(f"Backup path does not exist: {item_path}")
                results[item_name] = False
                continue
            
            # Apply defaults if fields are missing
            if 'include' not in item:
                item['include'] = ["**"]
            if 'exclude' not in item:
                item['exclude'] = []
            if 'excludeCaches' not in item:
                item['excludeCaches'] = True
            if 'excludeIfPresent' not in item:
                item['excludeIfPresent'] = ".nobackup"
            
            # Build filter files
            include_file, exclude_file = self.build_filter_files(item, global_excludes)
            
            # Build restic arguments
            args = ["backup"]
            
            if include_file:
                args.extend(["--files-from", include_file])
            else:
                args.append(item_path)
            
            if exclude_file:
                args.extend(["--exclude-file", exclude_file])
            
            # Add tags
            args.extend(["--tag", today_tag, "--tag", f"crd:{item_name}"])
            
            # Add optional parameters
            if item.get('excludeLargerThan'):
                args.extend(["--exclude-larger-than", item['excludeLargerThan']])
            if item.get('excludeCaches'):
                args.append("--exclude-caches")
            if item.get('excludeIfPresent'):
                args.extend(["--exclude-if-present", item['excludeIfPresent']])
            
            # Add standard options
            args.append("--one-file-system")
            
            # Execute backup
            success, stdout, stderr = self.run_restic_command(args)
            results[item_name] = success
            
            if success:
                self.logger.info(f"✓ Backup completed: {item_name}")
            else:
                self.logger.error(f"✗ Backup failed: {item_name}")
        
        return results
    
    def execute_restore(self, items: List[Dict[str, Any]], global_excludes: List[str]) -> Dict[str, bool]:
        """Execute restore operations for all items"""
        restore_date = os.environ.get('RESTORE_DATE', '2025-05-30')
        restore_timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        
        self.logger.info(f"Starting restore execution for {len(items)} items")
        self.logger.info(f"Restore date: {restore_date}")
        self.logger.info(f"Restore timestamp: {restore_timestamp}")
        
        results = {}
        backup_tag_date = restore_date.replace('-', '')
        
        for item in items:
            item_name = item['name']
            
            self.logger.info(f"Restoring {item_name} from {restore_date}")
            
            # Find snapshots for the specified date
            success, stdout, stderr = self.run_restic_command([
                "snapshots", "--tag", f"backup-{backup_tag_date}", 
                "--tag", f"crd:{item_name}", "--json"
            ])
            
            if not success or not stdout.strip():
                self.logger.error(f"No snapshots found for {item_name} on {restore_date}")
                results[item_name] = False
                continue
            
            try:
                snapshots = json.loads(stdout)
                if not snapshots:
                    self.logger.error(f"No snapshots found for {item_name} on {restore_date}")
                    results[item_name] = False
                    continue
                
                # Use the latest snapshot for that date
                snapshot_id = snapshots[-1]['short_id']
                self.logger.info(f"Found snapshot {snapshot_id} for {item_name}")
                
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                self.logger.error(f"Failed to parse snapshots for {item_name}: {e}")
                results[item_name] = False
                continue
            
            # Create restore directory
            restore_dir = f"/restored-data/{restore_timestamp}/{item_name}"
            os.makedirs(restore_dir, exist_ok=True)
            
            # Apply defaults if fields are missing (same as backup)
            if 'include' not in item:
                item['include'] = ["**"]
            if 'exclude' not in item:
                item['exclude'] = []
            if 'excludeCaches' not in item:
                item['excludeCaches'] = True
            if 'excludeIfPresent' not in item:
                item['excludeIfPresent'] = ".nobackup"
            
            # Build filter files (same as backup)
            include_file, exclude_file = self.build_filter_files(item, global_excludes)
            
            # Build restore arguments
            args = ["restore", snapshot_id, "--target", restore_dir]
            
            if include_file:
                # For restore, we need to include the original path structure
                args.extend(["--include", f"{item['path']}/**"])
                for pattern in item.get('include', []):
                    args.extend(["--include", f"{item['path']}/{pattern}"])
            
            if exclude_file:
                args.extend(["--exclude-file", exclude_file])
            
            # Execute restore
            success, stdout, stderr = self.run_restic_command(args)
            results[item_name] = success
            
            if success:
                self.logger.info(f"✓ Restore completed: {item_name} -> {restore_dir}")
            else:
                self.logger.error(f"✗ Restore failed: {item_name}")
        
        return results
    
    def cleanup(self):
        """Cleanup temporary files"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)
    
    def run_maintenance(self):
        """Run repository maintenance after successful operations"""
        self.logger.info("Running repository maintenance...")
        
        # Integrity check
        self.logger.info("Verifying repository integrity...")
        success, stdout, stderr = self.run_restic_command(["check", "--read-data-subset=5%"])
        if not success:
            self.logger.warning(f"Integrity check failed: {stderr}")
        
        # Cleanup old snapshots
        self.logger.info("Cleaning up old snapshots...")
        success, stdout, stderr = self.run_restic_command([
            "forget", "--keep-daily", "30", "--keep-weekly", "7", 
            "--keep-monthly", "12", "--prune"
        ])
        if not success:
            self.logger.warning(f"Snapshot cleanup failed: {stderr}")
    
    def main(self, operation: str, label_selector: str) -> int:
        """Main execution function"""
        try:
            self.logger.info(f"Starting {operation} operation with selector: {label_selector}")
            
            # Load global configuration
            global_config = self.load_global_config()
            global_excludes = global_config.get('globalExcludes', [])
            
            # Discover CRDs
            items = self.discover_crds(label_selector)
            
            if not items:
                self.logger.warning(f"No CRDs found with selector: {label_selector}")
                return 0
            
            # Execute operation
            if operation == "backup":
                results = self.execute_backup(items, global_excludes)
            elif operation == "restore":
                results = self.execute_restore(items, global_excludes)
            else:
                raise ResticOperationsError(f"Unknown operation: {operation}")
            
            # Report results
            successful = sum(1 for success in results.values() if success)
            failed = len(results) - successful
            
            self.logger.info(f"Operation completed - Success: {successful}, Failed: {failed}")
            
            # Run maintenance if any operations succeeded
            if successful > 0 and operation == "backup":
                self.run_maintenance()
            
            return 1 if failed > 0 else 0
            
        except ResticOperationsError as e:
            self.logger.error(f"Operation failed: {e}")
            return 1
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}", exc_info=True)
            return 1
        finally:
            self.cleanup()

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Restic backup and restore operations")
    parser.add_argument("operation", choices=["backup", "restore"], 
                       help="Operation to perform")
    parser.add_argument("label_selector", 
                       help="Kubernetes label selector for CRD discovery")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    operations = ResticOperations()
    sys.exit(operations.main(args.operation, args.label_selector))