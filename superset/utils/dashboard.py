# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_dashboard_title(dashboard_data: dict[str, Any]) -> str:
    """Extract and normalize dashboard title from metadata."""
    return dashboard_data.get("dashboard_title", "").strip()


def get_dashboard_filter_ids(
    metadata: dict[str, Any],
) -> list[int]:
    """Return chart IDs referenced in filter scopes."""
    filter_scopes = metadata.get("filter_scopes", {})
    return [int(chart_id) for chart_id in filter_scopes]
