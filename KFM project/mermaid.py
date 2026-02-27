flowchart TD
    %% Define styles
    classDef core fill:#f9f,stroke:#333,stroke-width:2px;
    classDef ble fill:#ff9,stroke:#333,stroke-width:2px;
    classDef flask fill:#9f9,stroke:#333,stroke-width:2px;
    classDef db fill:#9ff,stroke:#333,stroke-width:2px;
    classDef reporting fill:#f99,stroke:#333,stroke-width:2px;

    %% Core System
    A[Main System Loop]:::core --> B[BLE Communication]:::ble
    A --> C[Flask Web Server]:::flask
    A --> D[Session Management]:::core
    A --> E[Reporting System]:::reporting

    %% BLE Communication
    B --> B1[Connect to BLE Device]
    B --> B2[Setup Notifications]
    B2 --> B3[Angle Callback]
    B2 --> B4[Max Angle Callback]
    B2 --> B5[Hold Time Callback]

    %% Flask Web Server
    C --> C1[Dashboard Endpoint]
    C --> C2[Real-Time Data Endpoint]
    C --> C3[Game 1 (Rocket Game)]
    C --> C4[Game 2 (Car Game)]
    C --> C5[Setup Endpoint]
    C --> C6[Shutdown Endpoint]
    C --> C7[Prescribed Exercises Endpoint]

    %% Session Management
    D --> D1[Start New Session]
    D --> D2[Update Session]
    D --> D3[Finalize Session]
    D --> D4[Session History]

    %% Reporting System
    E --> E1[Generate KFMS Plot]
    E --> E2[Create PDF Report]
    E --> E3[Email Report]

    %% Database Operations
    D4 --> F[SQLite Database]:::db
    F --> F1[Store Session Data]
    F --> F2[Retrieve Session Data]

flowchart TD
    %% Define styles
    classDef ble fill:#ff9,stroke:#333,stroke-width:2px;
    classDef data fill:#9ff,stroke:#333,stroke-width:2px;

    %% BLE Communication
    A[BLE Communication]:::ble --> B[Connect to BLE Device]
    A --> C[Setup Notifications]
    C --> D[Angle Callback]
    C --> E[Max Angle Callback]
    C --> F[Hold Time Callback]

    %% Data Processing
    D --> G[Process Angle Data]:::data
    E --> H[Process Max Angle Data]:::data
    F --> I[Process Hold Time Data]:::data

    %% Data Flow
    G --> J[Update Session]
    H --> J
    I --> J

flowchart TD
    %% Define styles
    classDef flask fill:#9f9,stroke:#333,stroke-width:2px;

    %% Flask Web Server
    A[Flask Web Server]:::flask --> B[Dashboard Endpoint]
    A --> C[Real-Time Data Endpoint]
    A --> D[Game 1 (Rocket Game)]
    A --> E[Game 2 (Car Game)]
    A --> F[Setup Endpoint]
    A --> G[Shutdown Endpoint]
    A --> H[Prescribed Exercises Endpoint]

    %% Endpoint Details
    B --> B1[Render Dashboard]
    C --> C1[Provide Live Data]
    D --> D1[Render Rocket Game]
    E --> E1[Render Car Game]
    F --> F1[Update Configuration]
    G --> G1[Trigger Shutdown]
    H --> H1[Provide Exercise Recommendations]

flowchart TD
    %% Define styles
    classDef core fill:#f9f,stroke:#333,stroke-width:2px;
    classDef db fill:#9ff,stroke:#333,stroke-width:2px;

    %% Session Management
    A[Session Management]:::core --> B[Start New Session]
    A --> C[Update Session]
    A --> D[Finalize Session]
    A --> E[Session History]

    %% Data Flow
    B --> F[Initialize Session Data]
    C --> G[Update Live Angles]
    D --> H[Calculate Metrics]
    H --> I[Stability Index]
    H --> J[KFMS Score]
    D --> K[Store Session Data]:::db
    E --> K

flowchart TD
    %% Define styles
    classDef reporting fill:#f99,stroke:#333,stroke-width:2px;
    classDef db fill:#9ff,stroke:#333,stroke-width:2px;

    %% Reporting System
    A[Reporting System]:::reporting --> B[Generate KFMS Plot]
    A --> C[Create PDF Report]
    A --> D[Email Report]

    %% Data Flow
    B --> E[Retrieve Data from Database]:::db
    C --> F[Include KFMS Plot]
    C --> G[Include Session Data Table]
    C --> H[Include Averages]
    D --> I[Attach PDF Report]

flowchart TD
    %% Define styles
    classDef db fill:#9ff,stroke:#333,stroke-width:2px;

    %% Database Operations
    A[SQLite Database]:::db --> B[Store Session Data]
    A --> C[Retrieve Session Data]

    %% Data Flow
    B --> D[Session Table]
    C --> D

