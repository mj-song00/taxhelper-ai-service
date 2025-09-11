package texhelper.chatservice.common.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
public class WebClientConfig {

    @Bean
    public WebClient lawWebClient() {
        return WebClient.builder()
                .baseUrl("https://www.law.go.kr/DRF")
                .build();
    }

}
