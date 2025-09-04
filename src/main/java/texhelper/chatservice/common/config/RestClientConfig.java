package texhelper.chatservice.common.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

@Configuration
public class RestClientConfig {


    @Bean
    public RestClient restClient (){
        return RestClient.builder()
                .baseUrl("http://www.law.go.kr/DRF/lawService.do?OC=minjirj&target=law&MST=127280&type=JSON")
                .build();
    }




}
