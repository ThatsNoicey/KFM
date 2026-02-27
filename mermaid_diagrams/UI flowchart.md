flowchart TD
    subgraph Dashboard [Dashboard]
        A1[Initial Modal]
        A2[Main Dashboard]
        A3[Side Panel]
    end

    subgraph Game [Rocket Game]
        B1[Game Page]
    end

    subgraph Finish [Car Game]
        C1[Game Page]
    end

    A1 --> A2 --> A3 --> A2
    A3 --> B1
    A3 --> C1

